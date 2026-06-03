#requires -Version 5.1
<#
.SYNOPSIS
    One-shot provisioning of the Entra app registration required by PULSAR.

.DESCRIPTION
    Creates (or updates) a single Entra app registration that covers everything PULSAR needs:

        Outbound — reading M365 audit data
            Microsoft Graph application permissions (admin consent granted automatically):
              AuditLog.Read.All
              DeviceManagementConfiguration.Read.All

            Office 365 Management Activity API application permission (admin consent granted):
              ActivityFeed.Read  — Exchange / SharePoint / Teams audit events

            A client secret for PULSAR to authenticate as this app (stored in CLIENT_SECRET).

        Inbound — protecting the PULSAR UI, REST API, and MCP endpoint
            Application ID URI: api://<clientId>
            Delegated scope:    user_impersonation  (requested by browser UI and MCP clients)
            Public client:      enabled             (PKCE flows for Claude Desktop / Cursor)
            Redirect URIs:      http://localhost                         (loopback — Cursor etc.)
                                https://claude.ai/api/mcp/auth_callback (Claude Desktop)

    The script outputs the exact lines to copy into your PULSAR .env, replacing the
    manual "fill in CLIENT_ID / CLIENT_SECRET" step in the deployment guide.

    EXISTING DEPLOYMENTS
    If you already have PULSAR running with a manually created app registration, use
    bootstrap-mcp-auth.ps1 instead — it adds only the inbound auth capabilities to
    your existing app without touching the client secret or Graph permissions.

.PARAMETER TenantName
    The Microsoft 365 tenant domain, e.g. contoso.onmicrosoft.com.

.PARAMETER AppDisplayName
    Display name for the app registration.  Default: "PULSAR".

.PARAMETER ExistingAppId
    App ID of an existing registration to update rather than create a new one.

.PARAMETER SecretValidityYears
    How long the client secret should be valid.  Default: 1.

.EXAMPLE
    # New deployment
    .\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com"

.EXAMPLE
    # Update an existing registration (e.g. rotate secret, add missing permissions)
    .\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -ExistingAppId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$TenantName,

    [string]$AppDisplayName = "PULSAR",

    [string]$ExistingAppId = "",

    [int]$SecretValidityYears = 1
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
Test-ModuleInstalled "Microsoft.Graph.Identity.SignIns"
Import-Module Microsoft.Graph.Applications
Import-Module Microsoft.Graph.Identity.SignIns

# ---------------------------------------------------------------------------
# Connect and resolve tenant
# ---------------------------------------------------------------------------

Write-Host "Connecting to Microsoft Graph..." -ForegroundColor Cyan
Connect-MgGraph -TenantId $TenantName `
    -Scopes "Application.ReadWrite.All","AppRoleAssignment.ReadWrite.All","Directory.Read.All" `
    -NoWelcome

$tenant = Get-MgOrganization | Select-Object -First 1
if (-not $tenant) { throw "Unable to read tenant details. Check your authentication." }
$tenantId = $tenant.Id
Write-Host "Tenant: $($tenant.DisplayName) ($tenantId)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Resolve required permissions
# ---------------------------------------------------------------------------

# --- Microsoft Graph (resourceAppId: 00000003-0000-0000-c000-000000000000) ---
$graphSp = Get-MgServicePrincipal -Filter "appId eq '00000003-0000-0000-c000-000000000000'"
if (-not $graphSp) { throw "Microsoft Graph service principal not found." }

$graphPermissions = @(
    "AuditLog.Read.All",
    "DeviceManagementConfiguration.Read.All"
)

$graphRoles = @()
foreach ($name in $graphPermissions) {
    $role = $graphSp.AppRoles | Where-Object { $_.Value -eq $name } | Select-Object -First 1
    if (-not $role) { Write-Warning "Graph permission '$name' not found — skipping." ; continue }
    $graphRoles += $role
}

# --- Office 365 Management APIs (resourceAppId: c5393580-f805-4401-95e8-94b7a6ef2fc2) ---
# Provides ActivityFeed.Read for Exchange / SharePoint / Teams audit events.
$o365MgmtAppId = "c5393580-f805-4401-95e8-94b7a6ef2fc2"
$o365Sp = Get-MgServicePrincipal -Filter "appId eq '$o365MgmtAppId'" | Select-Object -First 1
if (-not $o365Sp) {
    Write-Warning "Office 365 Management APIs service principal not found in tenant — skipping ActivityFeed.Read."
    Write-Warning "This is expected on tenants where no app has previously consented to this API."
    Write-Warning "You can add it manually: App registration → API permissions → Add → APIs my organisation uses → Office 365 Management APIs → ActivityFeed.Read."
    $o365Roles = @()
} else {
    $activityFeedRole = $o365Sp.AppRoles | Where-Object { $_.Value -eq "ActivityFeed.Read" } | Select-Object -First 1
    if (-not $activityFeedRole) {
        Write-Warning "ActivityFeed.Read not found on Office 365 Management APIs SP — skipping."
        $o365Roles = @()
    } else {
        $o365Roles = @($activityFeedRole)
    }
}

# Build RequiredResourceAccess
$requiredResourceAccess = @()
if ($graphRoles.Count -gt 0) {
    $requiredResourceAccess += @{
        resourceAppId  = $graphSp.AppId
        resourceAccess = @($graphRoles | ForEach-Object { @{ id = $_.Id; type = "Role" } })
    }
}
if ($o365Roles.Count -gt 0) {
    $requiredResourceAccess += @{
        resourceAppId  = $o365MgmtAppId
        resourceAccess = @($o365Roles | ForEach-Object { @{ id = $_.Id; type = "Role" } })
    }
}

# ---------------------------------------------------------------------------
# Resolve or create the app registration
# ---------------------------------------------------------------------------

if ($ExistingAppId) {
    $app = Get-MgApplication -Filter "appId eq '$ExistingAppId'" | Select-Object -First 1
    if (-not $app) { throw "No app registration found with App ID '$ExistingAppId'." }
    Write-Host "Updating existing app: $($app.DisplayName) ($($app.AppId))" -ForegroundColor Yellow
} else {
    $app = Get-MgApplication -Filter "displayName eq '$AppDisplayName'" | Select-Object -First 1
    if ($app) {
        Write-Host "Found existing app registration '$AppDisplayName': $($app.AppId)" -ForegroundColor Yellow
    } else {
        Write-Host "Creating app registration: $AppDisplayName" -ForegroundColor Cyan
        $app = New-MgApplication `
            -DisplayName $AppDisplayName `
            -SignInAudience "AzureADMyOrg" `
            -RequiredResourceAccess $requiredResourceAccess
        Write-Host "Created. App ID: $($app.AppId)" -ForegroundColor Green
    }
}

$appId  = $app.AppId
$appOid = $app.Id

# Update permissions on existing app
if ($ExistingAppId -or ($app.DisplayName -eq $AppDisplayName)) {
    Update-MgApplication -ApplicationId $appOid -RequiredResourceAccess $requiredResourceAccess
    Write-Host "API permissions updated." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Ensure a service principal exists
# ---------------------------------------------------------------------------

$sp = Get-MgServicePrincipal -Filter "appId eq '$appId'" | Select-Object -First 1
if (-not $sp) {
    Write-Host "Creating service principal..." -ForegroundColor Cyan
    $sp = New-MgServicePrincipal -AppId $appId
}

# ---------------------------------------------------------------------------
# Grant admin consent
# ---------------------------------------------------------------------------

Write-Host "Granting admin consent for Graph permissions..." -ForegroundColor Cyan
foreach ($role in $graphRoles) {
    $existing = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id |
        Where-Object { $_.AppRoleId -eq $role.Id }
    if (-not $existing) {
        New-MgServicePrincipalAppRoleAssignment `
            -ServicePrincipalId $sp.Id `
            -PrincipalId $sp.Id `
            -ResourceId $graphSp.Id `
            -AppRoleId $role.Id | Out-Null
    }
}
Write-Host "Graph admin consent granted." -ForegroundColor Green

if ($o365Roles.Count -gt 0 -and $o365Sp) {
    Write-Host "Granting admin consent for Office 365 Management APIs..." -ForegroundColor Cyan
    foreach ($role in $o365Roles) {
        $existing = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id |
            Where-Object { $_.AppRoleId -eq $role.Id }
        if (-not $existing) {
            New-MgServicePrincipalAppRoleAssignment `
                -ServicePrincipalId $sp.Id `
                -PrincipalId $sp.Id `
                -ResourceId $o365Sp.Id `
                -AppRoleId $role.Id | Out-Null
        }
    }
    Write-Host "Office 365 Management APIs admin consent granted." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Client secret (rotate if already exists under the same display name)
# ---------------------------------------------------------------------------

$secretDesc   = "PulsarClientSecret"
$appWithCreds = Get-MgApplication -ApplicationId $appOid -Property "id,passwordCredentials"
$oldSecrets   = $appWithCreds.PasswordCredentials | Where-Object { $_.DisplayName -eq $secretDesc }
foreach ($cred in $oldSecrets) {
    Write-Host "Removing old client secret ($($cred.KeyId))..." -ForegroundColor Yellow
    Remove-MgApplicationPassword -ApplicationId $appOid -BodyParameter @{ keyId = $cred.KeyId }
}

Write-Host "Creating client secret (valid $SecretValidityYears year(s))..." -ForegroundColor Cyan
$secretObj = Add-MgApplicationPassword -ApplicationId $appOid -BodyParameter @{
    displayName = $secretDesc
    endDateTime = (Get-Date).AddYears($SecretValidityYears).ToString("o")
}
$clientSecret = $secretObj.SecretText
Write-Host "Client secret created." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Set Application ID URI (required for api://<clientId>/user_impersonation scope)
# ---------------------------------------------------------------------------

$appIdUri        = "api://$appId"
$currentUris     = (Get-MgApplication -ApplicationId $appOid).IdentifierUris
if ($currentUris -contains $appIdUri) {
    Write-Host "Application ID URI already set — skipping." -ForegroundColor Yellow
} else {
    $updatedUris = @($appIdUri) + ($currentUris | Where-Object { $_ -ne $appIdUri })
    Update-MgApplication -ApplicationId $appOid -IdentifierUris $updatedUris
    Write-Host "Application ID URI set to $appIdUri." -ForegroundColor Green
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
# Generate MCP_API_KEY suggestion
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
Write-Host "App:                   $AppDisplayName"
Write-Host "App ID (client ID):    $appId"
Write-Host "Application ID URI:    $appIdUri"
Write-Host "Tenant ID:             $tenantId"
Write-Host ""
Write-Host "--- Copy these lines into your PULSAR .env ---" -ForegroundColor Cyan
Write-Host ""
Write-Host "# Outbound — M365 audit data ingestion"
Write-Host "TENANT_ID=$tenantId"
Write-Host "CLIENT_ID=$appId"
Write-Host "CLIENT_SECRET=$clientSecret"
Write-Host ""
Write-Host "# Inbound — protect the UI, REST API, and MCP endpoint"
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
Write-Host ""
Write-Host "----------------------------------------------" -ForegroundColor Cyan
Write-Host ""
Write-Host "IMPORTANT: CLIENT_SECRET is shown only once. Copy it now." -ForegroundColor Yellow
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Copy the .env lines above."
Write-Host "  2. Optionally set AUTH_ALLOWED_ROLES or AUTH_ALLOWED_GROUPS to restrict access."
Write-Host "  3. Start PULSAR: docker compose -f docker-compose.prod.yml up -d"
if ($o365Roles.Count -eq 0) {
    Write-Host ""
    Write-Host "  NOTE: ActivityFeed.Read (Office 365 Management APIs) was not added automatically." -ForegroundColor Yellow
    Write-Host "  Add it manually: App registration → API permissions → Add → APIs my organisation uses"
    Write-Host "  → Office 365 Management APIs → Application permissions → ActivityFeed.Read → Grant admin consent." -ForegroundColor Yellow
}

Disconnect-MgGraph | Out-Null
