#requires -Version 5.1
<#
.SYNOPSIS
    Configures the Entra ID app registration for PULSAR — covering both the web UI/API
    and the MCP SSE endpoint from a single registration.

.DESCRIPTION
    PULSAR uses one Entra app registration for everything: the UI login flow, the REST API
    auth (AUTH_CLIENT_ID), and the MCP OAuth PKCE flow (MCP_CLIENT_ID).  They are the same
    app.  Running two separate registrations would mean two different audiences, which breaks
    the in-process JWT validation.

    This script adds or ensures the following on that registration:

        1. Application ID URI set to api://<clientId>  (required for named scopes)
        2. Delegated scope: user_impersonation         (requested by Claude Desktop / Cursor)
        3. Public client / PKCE enabled                (required for OAuth code + PKCE flow)
        4. Loopback redirect URI registered            (http://localhost, covers any port)

    TWO USAGE MODES:

        Existing Entra auth (AUTH_CLIENT_ID already in .env)
            Pass -ExistingAppId with the app ID already in AUTH_CLIENT_ID.
            The script adds MCP capabilities to that app and changes nothing else.
            AUTH_CLIENT_ID stays the same — no .env edits needed beyond MCP_CLIENT_ID.

            .\bootstrap-mcp-auth.ps1 -TenantName "contoso.onmicrosoft.com" `
                                     -ExistingAppId "<your-existing-AUTH_CLIENT_ID>"

        No Entra auth yet (fresh deployment)
            Run without -ExistingAppId.  The script creates a new "PULSAR" app registration
            fully configured for both UI/API auth and MCP, then outputs all .env lines.

            .\bootstrap-mcp-auth.ps1 -TenantName "contoso.onmicrosoft.com"

    The script does NOT create a client secret.  PULSAR validates tokens in-process using
    the tenant's public JWKS endpoint — no secret is needed server-side.

    MCP_API_KEY (static bearer token) is a separate, simpler auth path that works
    independently of Entra.  The script generates a suggested value.  Set it regardless
    of whether you use Entra — it gives Claude Desktop and service callers a zero-friction
    option that does not require an OAuth flow.

.PARAMETER TenantName
    The Microsoft 365 tenant domain, e.g. contoso.onmicrosoft.com.

.PARAMETER ExistingAppId
    App ID of the existing PULSAR app registration (the value in AUTH_CLIENT_ID).
    Required when PULSAR already has Entra auth configured.

.PARAMETER AppDisplayName
    Display name when creating a new registration.  Default: "PULSAR".
    Ignored when -ExistingAppId is provided.

.EXAMPLE
    # Existing Entra auth — add MCP capabilities to the app already in AUTH_CLIENT_ID
    .\bootstrap-mcp-auth.ps1 -TenantName "contoso.onmicrosoft.com" -ExistingAppId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

.EXAMPLE
    # Fresh deployment — create a new fully configured registration
    .\bootstrap-mcp-auth.ps1 -TenantName "contoso.onmicrosoft.com"
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$TenantName,

    [string]$ExistingAppId = "",

    [string]$AppDisplayName = "PULSAR"
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Module bootstrap
# ---------------------------------------------------------------------------

function Test-ModuleInstalled {
    param ([string]$Name)
    if (-not (Get-Module -ListAvailable -Name $Name | Select-Object -First 1)) {
        Write-Host "Installing module: $Name" -ForegroundColor Cyan
        Install-Module $Name -Scope CurrentUser -Force -AllowClobber
    }
}

Test-ModuleInstalled "Microsoft.Graph.Applications"
Import-Module Microsoft.Graph.Applications

# ---------------------------------------------------------------------------
# Connect and resolve tenant
# ---------------------------------------------------------------------------

Write-Host "Connecting to Microsoft Graph..." -ForegroundColor Cyan
Connect-MgGraph -TenantId $TenantName -Scopes "Application.ReadWrite.All" -NoWelcome

$tenant = Get-MgOrganization | Select-Object -First 1
if (-not $tenant) { throw "Unable to read tenant details. Check your authentication." }
$tenantId = $tenant.Id
Write-Host "Tenant: $($tenant.DisplayName) ($tenantId)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Resolve or create the app registration
# ---------------------------------------------------------------------------

$isExisting = -not [string]::IsNullOrWhiteSpace($ExistingAppId)

if ($isExisting) {
    $app = Get-MgApplication -Filter "appId eq '$ExistingAppId'" | Select-Object -First 1
    if (-not $app) { throw "No app registration found with App ID '$ExistingAppId'. Check the value matches AUTH_CLIENT_ID in your .env." }
    Write-Host "Using existing app: $($app.DisplayName) ($($app.AppId))" -ForegroundColor Yellow
    Write-Host "This is the same app as AUTH_CLIENT_ID — MCP capabilities will be added without changing anything else." -ForegroundColor DarkGray
} else {
    $app = Get-MgApplication -Filter "displayName eq '$AppDisplayName'" | Select-Object -First 1
    if ($app) {
        Write-Host "Found existing app registration '$AppDisplayName': $($app.AppId)" -ForegroundColor Yellow
        Write-Host "Tip: if this is not the right app, pass -ExistingAppId with the correct app ID." -ForegroundColor DarkGray
    } else {
        Write-Host "Creating app registration: $AppDisplayName" -ForegroundColor Cyan
        $app = New-MgApplication `
            -DisplayName $AppDisplayName `
            -SignInAudience "AzureADMyOrg"
        Write-Host "Created. App ID: $($app.AppId)" -ForegroundColor Green
    }
}

$appId    = $app.AppId
$appOid   = $app.Id
$appIdUri = "api://$appId"

# ---------------------------------------------------------------------------
# Set the Application ID URI (required for api://<id>/user_impersonation scope)
# ---------------------------------------------------------------------------

$currentUris = $app.IdentifierUris
if ($currentUris -contains $appIdUri) {
    Write-Host "Application ID URI already set to $appIdUri — skipping." -ForegroundColor Yellow
} else {
    Write-Host "Setting Application ID URI to $appIdUri..." -ForegroundColor Cyan
    # Preserve any existing URIs (e.g. custom domains) and add ours.
    $updatedUris = @($appIdUri) + ($currentUris | Where-Object { $_ -ne $appIdUri })
    Update-MgApplication -ApplicationId $appOid -IdentifierUris $updatedUris
    Write-Host "Application ID URI set." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Expose delegated scope: user_impersonation
# ---------------------------------------------------------------------------

$appFull        = Get-MgApplication -ApplicationId $appOid
$existingScopes = $appFull.Api.Oauth2PermissionScopes
$userImpScope   = $existingScopes | Where-Object { $_.Value -eq "user_impersonation" } | Select-Object -First 1

if ($userImpScope) {
    Write-Host "user_impersonation scope already exists — skipping." -ForegroundColor Yellow
} else {
    Write-Host "Adding user_impersonation scope..." -ForegroundColor Cyan
    $newScope = @{
        Id                      = [System.Guid]::NewGuid().ToString()
        Value                   = "user_impersonation"
        AdminConsentDisplayName = "Access PULSAR"
        AdminConsentDescription = "Allows the application to query M365 audit events via PULSAR on behalf of the signed-in user."
        UserConsentDisplayName  = "Access PULSAR"
        UserConsentDescription  = "Allows this app to query M365 audit events via PULSAR on your behalf."
        IsEnabled               = $true
        Type                    = "User"
    }
    $updatedScopes = @($newScope)
    if ($existingScopes) { $updatedScopes += $existingScopes }
    Update-MgApplication -ApplicationId $appOid -Api @{ oauth2PermissionScopes = $updatedScopes }
    Write-Host "Scope added." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Redirect URIs
#
# Public client (Mobile and desktop applications platform):
#   http://localhost — loopback, covers any port per RFC 8252 §8.3.
#                      Used by Cursor and other local MCP clients.
#
# Web platform:
#   https://claude.ai/api/mcp/auth_callback — Claude Desktop's OAuth callback.
#                      Claude Desktop completes the auth code flow server-side
#                      via claude.ai, so this must be a web redirect URI.
# ---------------------------------------------------------------------------

Write-Host "Configuring redirect URIs..." -ForegroundColor Cyan
$appForRedirects = Get-MgApplication -ApplicationId $appOid

# Public client (loopback)
$existingPublicRedirects = $appForRedirects.PublicClient.RedirectUris
if ($existingPublicRedirects -contains "http://localhost") {
    Write-Host "Loopback redirect URI already registered — skipping." -ForegroundColor Yellow
} else {
    $updatedPublicRedirects = @("http://localhost") + ($existingPublicRedirects | Where-Object { $_ -ne "http://localhost" })
    Update-MgApplication -ApplicationId $appOid `
        -IsFallbackPublicClient:$true `
        -PublicClient @{ redirectUris = $updatedPublicRedirects }
    Write-Host "Public client (loopback) configured." -ForegroundColor Green
}

# Web platform (Claude Desktop callback)
$claudeCallback = "https://claude.ai/api/mcp/auth_callback"
$existingWebRedirects = $appForRedirects.Web.RedirectUris
if ($existingWebRedirects -contains $claudeCallback) {
    Write-Host "Claude Desktop redirect URI already registered — skipping." -ForegroundColor Yellow
} else {
    $updatedWebRedirects = @($claudeCallback) + ($existingWebRedirects | Where-Object { $_ -ne $claudeCallback })
    Update-MgApplication -ApplicationId $appOid `
        -Web @{ redirectUris = $updatedWebRedirects }
    Write-Host "Claude Desktop redirect URI registered." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Ensure a service principal exists
# ---------------------------------------------------------------------------

$sp = Get-MgServicePrincipal -Filter "appId eq '$appId'" | Select-Object -First 1
if (-not $sp) {
    Write-Host "Creating service principal..." -ForegroundColor Cyan
    $sp = New-MgServicePrincipal -AppId $appId
    Write-Host "Service principal created." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Generate an MCP_API_KEY suggestion
# ---------------------------------------------------------------------------

$suggestedApiKey = [System.Convert]::ToBase64String(
    [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).TrimEnd("=").Replace("+", "-").Replace("/", "_")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "=== Bootstrap complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "App:                $($app.DisplayName)"
Write-Host "App ID (client ID): $appId"
Write-Host "Application ID URI: $appIdUri"
Write-Host "Tenant ID:          $tenantId"
Write-Host ""

if ($isExisting) {
    Write-Host "--- MCP capabilities added to your existing app ---" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "AUTH_CLIENT_ID is unchanged — it already points to this app."
    Write-Host "Add only these lines to your PULSAR .env:"
    Write-Host ""
    Write-Host "# OAuth discovery — enables Claude Desktop to auto-discover Entra auth"
    Write-Host "MCP_CLIENT_ID=$appId"
    Write-Host ""
    Write-Host "# API key — zero-friction alternative for Claude Desktop and service callers"
    Write-Host "MCP_API_KEY=$suggestedApiKey"
} else {
    Write-Host "--- Copy these lines into your PULSAR .env ---" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "# Entra ID auth (protects UI, REST API, and MCP endpoint)"
    Write-Host "AUTH_ENABLED=true"
    Write-Host "AUTH_TENANT_ID=$tenantId"
    Write-Host "AUTH_CLIENT_ID=$appId"
    Write-Host "AUTH_SCOPE=$appIdUri/user_impersonation"
    Write-Host ""
    Write-Host "# OAuth discovery — enables Claude Desktop to auto-discover Entra auth"
    Write-Host "MCP_CLIENT_ID=$appId"
    Write-Host ""
    Write-Host "# API key — zero-friction alternative for Claude Desktop and service callers"
    Write-Host "MCP_API_KEY=$suggestedApiKey"
}

Write-Host ""
Write-Host "----------------------------------------------" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Add the .env lines above, then restart PULSAR."
Write-Host "  2. Claude Desktop (API key — simplest):"
Write-Host '     "headers": { "Authorization": "Bearer <MCP_API_KEY>" }'
Write-Host "  3. Claude Desktop (Entra OAuth — no static key):"
Write-Host "     Set url only — Claude Desktop discovers auth via /.well-known/oauth-authorization-server."
Write-Host ""
Write-Host "Note: AUTH_CLIENT_ID accepts the bare GUID or api://<GUID> — both work." -ForegroundColor DarkGray

Disconnect-MgGraph | Out-Null
