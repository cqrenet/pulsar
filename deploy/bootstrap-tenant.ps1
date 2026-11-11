#requires -Version 7.0
<#
.SYNOPSIS
    Provisions the two Entra app registrations PULSAR needs.

.DESCRIPTION
    PULSAR uses two app registrations, so the credential that can read the whole tenant's audit
    data is never on the app that users and MCP clients sign in to.

        PULSAR Ingestion  (outbound: reads M365 audit data)
            Application permissions, admin consent granted:
              Microsoft Graph:             AuditLog.Read.All, DeviceManagementConfiguration.Read.All
              Office 365 Management APIs:  ActivityFeed.Read
            Credential: a certificate. You generate the private key ON THE PULSAR SERVER; this
            script uploads only the public certificate. No client secret, no redirect URIs,
            public client flows off.

        PULSAR  (inbound: protects the UI, REST API, and MCP endpoint)
            Application ID URI api://<clientId>, delegated scope user_impersonation.
            App roles:  PulsarReader, PulsarAdmin, PulsarPrivacyReader.
            Assignment required: only users assigned a role can get a token.
            Redirect URIs: SPA (-PublicUrl), http://localhost (Cursor etc.),
                           https://claude.ai/api/mcp/auth_callback (Claude Desktop).
            Public client flows: on. Claude Desktop redeems the code server-side without a secret.
            This app has no credentials and no application permissions.

    Re-running the script is safe: it only adds what is missing. The certificate is replaced
    only when you pass -PublicCertificatePath.

    BEFORE YOU RUN IT: create the ingestion key and certificate on the PULSAR server
        mkdir -p secrets && cd secrets
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=PULSAR Ingestion" \
            -keyout key.pem -out pulsar-ingestion.crt
        cat key.pem pulsar-ingestion.crt > pulsar-ingestion.pem && rm key.pem
        sudo chown 10001:10001 pulsar-ingestion.pem && sudo chmod 400 pulsar-ingestion.pem
    Copy only pulsar-ingestion.crt (the public certificate) to the machine running this script.

    MIGRATING FROM THE OLD SINGLE-APP SETUP
    Pass -ExistingAppId with your current CLIENT_ID. That app becomes the API app (its client ID
    and scope stay the same, so users and MCP clients need no changes). Once PULSAR runs with the
    new ingestion app, run again with -RemoveLegacyCredentials to delete the old client secret
    and application permissions from it.

.PARAMETER TenantName
    The Microsoft 365 tenant domain, e.g. contoso.onmicrosoft.com.

.PARAMETER PublicUrl
    PULSAR's public URL, e.g. https://pulsar.contoso.com. Registered as the SPA redirect URI.

.PARAMETER PublicCertificatePath
    Public certificate (PEM or DER) for the ingestion app. Replaces any certificate already
    registered on it. Required the first time.

.PARAMETER ExistingAppId
    App ID of an existing PULSAR app registration to use as the API app.

.PARAMETER RemoveLegacyCredentials
    Delete client secrets and application permissions from the API app (left over from the
    single-app setup). Irreversible: run it only after the ingestion app works.

.EXAMPLE
    # New deployment
    .\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -PublicUrl "https://pulsar.contoso.com" `
        -PublicCertificatePath .\pulsar-ingestion.crt

.EXAMPLE
    # Migrate the old single app, then remove its secret once ingestion works
    .\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -ExistingAppId "<CLIENT_ID>" `
        -PublicCertificatePath .\pulsar-ingestion.crt
    .\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -ExistingAppId "<CLIENT_ID>" -RemoveLegacyCredentials

.EXAMPLE
    # Rotate the ingestion certificate (create a new one on the server first)
    .\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -PublicCertificatePath .\pulsar-ingestion.crt
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$TenantName,

    [string]$PublicUrl = "",

    [string]$PublicCertificatePath = "",

    [string]$ExistingAppId = "",

    [string]$ApiAppDisplayName = "PULSAR",

    [string]$IngestionAppDisplayName = "PULSAR Ingestion",

    [switch]$RemoveLegacyCredentials
)

$ErrorActionPreference = "Stop"

$GraphAppId   = "00000003-0000-0000-c000-000000000000"
$O365MgmtAppId = "c5393580-f805-4401-95e8-94b7a6ef2fc2"

# App roles on the API app. Map them in .env (see the summary at the end).
$AppRoleDefinitions = @(
    @{ Value = "PulsarReader";        DisplayName = "PULSAR Reader";         Description = "Search audit events and triage alerts." },
    @{ Value = "PulsarAdmin";         DisplayName = "PULSAR Admin";          Description = "Reader access plus managing alert rules and triggering fetches." },
    @{ Value = "PulsarPrivacyReader"; DisplayName = "PULSAR Privacy Reader"; Description = "See privacy-gated services and operations (PRIVACY_SERVICES)." }
)

# ---------------------------------------------------------------------------
# Module bootstrap and connection
# ---------------------------------------------------------------------------

foreach ($name in "Microsoft.Graph.Applications", "Microsoft.Graph.Users", "Microsoft.Graph.Identity.DirectoryManagement") {
    if (-not (Get-Module -ListAvailable -Name $name | Select-Object -First 1)) {
        Write-Host "Installing module: $name" -ForegroundColor Cyan
        Install-Module $name -Scope CurrentUser -Force -AllowClobber
    }
    Import-Module $name
}

if (-not $PublicCertificatePath -and -not $RemoveLegacyCredentials) {
    Write-Host "No -PublicCertificatePath given: the ingestion certificate will not be changed." -ForegroundColor Yellow
}
$newCert = $null
if ($PublicCertificatePath) {
    $certFile = (Resolve-Path $PublicCertificatePath).Path
    if ((Get-Content -Raw $certFile) -match "PRIVATE KEY") {
        throw "$PublicCertificatePath contains a private key. Pass only the public certificate (.crt); the key stays on the server."
    }
    $newCert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $certFile
}

# @($null) is a one-element array in PowerShell; Graph rejects nulls inside collections.
function AsArray { param ($Value) return ,@($Value | Where-Object { $_ }) }  # comma: keep 1-item arrays as arrays

Write-Host "Connecting to Microsoft Graph..." -ForegroundColor Cyan
Connect-MgGraph -TenantId $TenantName `
    -Scopes "Application.ReadWrite.All", "AppRoleAssignment.ReadWrite.All", "Directory.Read.All" `
    -NoWelcome

$tenant = Get-MgOrganization | Select-Object -First 1
if (-not $tenant) { throw "Unable to read tenant details. Check your authentication." }
$tenantId = $tenant.Id
Write-Host "Tenant: $($tenant.DisplayName) ($tenantId)" -ForegroundColor Green

function Get-OrCreateApp {
    param ([string]$AppId, [string]$DisplayName)
    if ($AppId) {
        $app = Get-MgApplication -Filter "appId eq '$AppId'" | Select-Object -First 1
        if (-not $app) { throw "No app registration found with App ID '$AppId'." }
    } else {
        $app = Get-MgApplication -Filter "displayName eq '$DisplayName'" | Select-Object -First 1
    }
    if ($app) {
        Write-Host "Using app registration '$($app.DisplayName)': $($app.AppId)" -ForegroundColor Yellow
    } else {
        $app = New-MgApplication -DisplayName $DisplayName -SignInAudience "AzureADMyOrg"
        Write-Host "Created app registration '$DisplayName': $($app.AppId)" -ForegroundColor Green
    }
    $sp = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" | Select-Object -First 1
    if (-not $sp) { $sp = New-MgServicePrincipal -AppId $app.AppId }
    return $app, $sp
}

# ---------------------------------------------------------------------------
# Resolve the application permissions the ingestion app needs
# ---------------------------------------------------------------------------

$graphSp = Get-MgServicePrincipal -Filter "appId eq '$GraphAppId'"
if (-not $graphSp) { throw "Microsoft Graph service principal not found." }
$graphRoles = @(
    foreach ($name in "AuditLog.Read.All", "DeviceManagementConfiguration.Read.All") {
        $role = $graphSp.AppRoles | Where-Object { $_.Value -eq $name } | Select-Object -First 1
        if ($role) { $role } else { Write-Warning "Graph permission '$name' not found — skipping." }
    }
)

$o365Sp = Get-MgServicePrincipal -Filter "appId eq '$O365MgmtAppId'" | Select-Object -First 1
$o365Roles = @()
if ($o365Sp) {
    $o365Roles = @($o365Sp.AppRoles | Where-Object { $_.Value -eq "ActivityFeed.Read" } | Select-Object -First 1)
} else {
    Write-Warning "Office 365 Management APIs service principal not found — ActivityFeed.Read must be added manually."
}

$requiredResourceAccess = @()
if ($graphRoles.Count) {
    $requiredResourceAccess += @{ resourceAppId = $GraphAppId; resourceAccess = @($graphRoles | ForEach-Object { @{ id = $_.Id; type = "Role" } }) }
}
if ($o365Roles.Count) {
    $requiredResourceAccess += @{ resourceAppId = $O365MgmtAppId; resourceAccess = @($o365Roles | ForEach-Object { @{ id = $_.Id; type = "Role" } }) }
}

# ---------------------------------------------------------------------------
# 1. Ingestion app: application permissions + certificate, nothing else
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "=== Ingestion app ===" -ForegroundColor Cyan
$ingestApp, $ingestSp = Get-OrCreateApp -DisplayName $IngestionAppDisplayName

Update-MgApplication -ApplicationId $ingestApp.Id `
    -RequiredResourceAccess $requiredResourceAccess `
    -IsFallbackPublicClient:$false
Write-Host "Application permissions set; public client flows off." -ForegroundColor Green

function Grant-AppPermissions {
    param ($ResourceSp, $Roles)
    $granted = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $ingestSp.Id
    foreach ($role in $Roles) {
        if (-not ($granted | Where-Object { $_.AppRoleId -eq $role.Id -and $_.ResourceId -eq $ResourceSp.Id })) {
            New-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $ingestSp.Id `
                -PrincipalId $ingestSp.Id -ResourceId $ResourceSp.Id -AppRoleId $role.Id | Out-Null
        }
    }
}
Grant-AppPermissions -ResourceSp $graphSp -Roles $graphRoles
if ($o365Sp) { Grant-AppPermissions -ResourceSp $o365Sp -Roles $o365Roles }
Write-Host "Admin consent granted." -ForegroundColor Green

if ($newCert) {
    # Replaces existing certificates: PULSAR fails to fetch until it runs with the matching key.
    Update-MgApplication -ApplicationId $ingestApp.Id -KeyCredentials @(@{
        type        = "AsymmetricX509Cert"
        usage       = "Verify"
        key         = $newCert.RawData
        displayName = "PulsarIngestion $($newCert.NotAfter.ToString('yyyy-MM-dd'))"
    })
    Write-Host "Certificate uploaded (thumbprint $($newCert.Thumbprint), expires $($newCert.NotAfter.ToString('yyyy-MM-dd')))." -ForegroundColor Green
}

$ingestCreds = Get-MgApplication -ApplicationId $ingestApp.Id -Property "keyCredentials,passwordCredentials"
if (-not $ingestCreds.KeyCredentials) {
    Write-Warning "The ingestion app has no certificate. Re-run with -PublicCertificatePath."
}
if ($ingestCreds.PasswordCredentials) {
    Write-Warning "The ingestion app has client secrets. PULSAR does not use them — delete them in the portal."
}

# ---------------------------------------------------------------------------
# 2. API app: scope, app roles, redirect URIs, assignment required
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "=== API app ===" -ForegroundColor Cyan
$apiApp, $apiSp = Get-OrCreateApp -AppId $ExistingAppId -DisplayName $ApiAppDisplayName
$apiApp = Get-MgApplication -ApplicationId $apiApp.Id
$appIdUri = "api://$($apiApp.AppId)"

if ($apiApp.IdentifierUris -notcontains $appIdUri) {
    Update-MgApplication -ApplicationId $apiApp.Id -IdentifierUris (@($appIdUri) + (AsArray $apiApp.IdentifierUris))
    Write-Host "Application ID URI set to $appIdUri." -ForegroundColor Green
}

$scopes = AsArray $apiApp.Api.Oauth2PermissionScopes
if (-not ($scopes | Where-Object { $_.Value -eq "user_impersonation" })) {
    $scopes += @{
        Id                      = [System.Guid]::NewGuid().ToString()
        Value                   = "user_impersonation"
        AdminConsentDisplayName = "Access PULSAR"
        AdminConsentDescription = "Allows the application to query M365 audit events via PULSAR on behalf of the signed-in user."
        UserConsentDisplayName  = "Access PULSAR"
        UserConsentDescription  = "Allows this app to query M365 audit events via PULSAR on your behalf."
        IsEnabled               = $true
        Type                    = "User"
    }
    Update-MgApplication -ApplicationId $apiApp.Id -Api @{ oauth2PermissionScopes = $scopes }
    Write-Host "Scope user_impersonation added." -ForegroundColor Green
}

$appRoles = AsArray $apiApp.AppRoles
$addedRoles = $false
foreach ($def in $AppRoleDefinitions) {
    if (-not ($appRoles | Where-Object { $_.Value -eq $def.Value })) {
        $appRoles += @{
            Id                 = [System.Guid]::NewGuid().ToString()
            Value              = $def.Value
            DisplayName        = $def.DisplayName
            Description        = $def.Description
            AllowedMemberTypes = @("User")
            IsEnabled          = $true
        }
        $addedRoles = $true
    }
}
if ($addedRoles) {
    Update-MgApplication -ApplicationId $apiApp.Id -AppRoles $appRoles
    Write-Host "App roles added." -ForegroundColor Green
}

$publicRedirects = AsArray $apiApp.PublicClient.RedirectUris
if ($publicRedirects -notcontains "http://localhost") { $publicRedirects += "http://localhost" }
$webRedirects = AsArray $apiApp.Web.RedirectUris
$claudeCallback = "https://claude.ai/api/mcp/auth_callback"
if ($webRedirects -notcontains $claudeCallback) { $webRedirects += $claudeCallback }
$spaRedirects = AsArray $apiApp.Spa.RedirectUris
if ($PublicUrl -and ($spaRedirects -notcontains $PublicUrl.TrimEnd("/"))) { $spaRedirects += $PublicUrl.TrimEnd("/") }
Update-MgApplication -ApplicationId $apiApp.Id `
    -IsFallbackPublicClient:$true `
    -PublicClient @{ redirectUris = $publicRedirects } `
    -Web @{ redirectUris = $webRedirects } `
    -Spa @{ redirectUris = $spaRedirects }
Write-Host "Redirect URIs set." -ForegroundColor Green

Update-MgServicePrincipal -ServicePrincipalId $apiSp.Id -AppRoleAssignmentRequired:$true
Write-Host "Assignment required: only users with a PULSAR role can sign in." -ForegroundColor Green

# Assign the person running this script, so the first admin is not locked out.
$me = Get-MgUser -UserId (Get-MgContext).Account
$apiApp = Get-MgApplication -ApplicationId $apiApp.Id  # role IDs are the same on the app and its SP
$myAssignments = Get-MgUserAppRoleAssignment -UserId $me.Id | Where-Object { $_.ResourceId -eq $apiSp.Id }
foreach ($value in "PulsarAdmin", "PulsarPrivacyReader") {
    $role = $apiApp.AppRoles | Where-Object { $_.Value -eq $value }
    if (-not $role -or ($myAssignments | Where-Object { $_.AppRoleId -eq $role.Id })) { continue }
    # New roles take a few seconds to reach the service principal; retry until they do.
    for ($attempt = 1; ; $attempt++) {
        try {
            New-MgUserAppRoleAssignment -UserId $me.Id -PrincipalId $me.Id -ResourceId $apiSp.Id -AppRoleId $role.Id | Out-Null
            break
        } catch {
            if ($attempt -ge 6) { throw }
            Start-Sleep -Seconds 10
        }
    }
    Write-Host "Assigned $value to $($me.UserPrincipalName)." -ForegroundColor Green
}

# Leftovers from the single-app setup: a secret and app permissions on the app users sign in to.
$apiCreds = Get-MgApplication -ApplicationId $apiApp.Id -Property "passwordCredentials,keyCredentials,requiredResourceAccess"
$legacyGrants = @(Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $apiSp.Id |
    Where-Object { $_.ResourceId -eq $graphSp.Id -or ($o365Sp -and $_.ResourceId -eq $o365Sp.Id) })
$hasLegacy = $apiCreds.PasswordCredentials -or $apiCreds.KeyCredentials -or $legacyGrants.Count

if ($hasLegacy -and $RemoveLegacyCredentials) {
    foreach ($cred in $apiCreds.PasswordCredentials) {
        Remove-MgApplicationPassword -ApplicationId $apiApp.Id -BodyParameter @{ keyId = $cred.KeyId }
    }
    if ($apiCreds.KeyCredentials) { Update-MgApplication -ApplicationId $apiApp.Id -KeyCredentials @() }
    foreach ($grant in $legacyGrants) {
        Remove-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $apiSp.Id -AppRoleAssignmentId $grant.Id
    }
    Update-MgApplication -ApplicationId $apiApp.Id -RequiredResourceAccess @()
    Write-Host "Removed legacy secrets, certificates and application permissions from the API app." -ForegroundColor Green
    $hasLegacy = $false
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

$suggestedApiKey = [System.Convert]::ToBase64String(
    [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).TrimEnd("=").Replace("+", "-").Replace("/", "_")

Write-Host ""
Write-Host "=== Bootstrap complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "--- PULSAR .env ---" -ForegroundColor Cyan
Write-Host ""
Write-Host "# Ingestion app (reads M365 audit data with the certificate in secrets/pulsar-ingestion.pem)"
Write-Host "TENANT_ID=$tenantId"
Write-Host "CLIENT_ID=$($ingestApp.AppId)"
Write-Host ""
Write-Host "# API app (UI, REST API, MCP)"
Write-Host "AUTH_ENABLED=true"
Write-Host "AUTH_TENANT_ID=$tenantId"
Write-Host "AUTH_CLIENT_ID=$($apiApp.AppId)"
Write-Host "AUTH_SCOPE=$appIdUri/user_impersonation"
Write-Host "AUTH_ALLOWED_ROLES=PulsarReader,PulsarAdmin"
Write-Host "AUTH_ADMIN_ROLES=PulsarAdmin"
Write-Host "PRIVACY_SERVICE_ROLES=PulsarPrivacyReader"
Write-Host "MCP_CLIENT_ID=$($apiApp.AppId)"
Write-Host "MCP_API_KEY=$suggestedApiKey   # only if you need a key for non-interactive MCP callers"
Write-Host ""
Write-Host "-------------------" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Put the lines above in .env on the server (remove any old CLIENT_SECRET line)."
Write-Host "  2. Assign PULSAR roles to users or groups: Entra admin center > Enterprise applications > $ApiAppDisplayName > Users and groups."
Write-Host "  3. Restart PULSAR: docker compose -f docker-compose.prod.yml up -d"
if ($hasLegacy) {
    Write-Host ""
    Write-Host "  The API app still has a client secret or application permissions from the old single-app setup." -ForegroundColor Yellow
    Write-Host "  Once PULSAR fetches events with the new ingestion app, remove them:" -ForegroundColor Yellow
    Write-Host "    .\bootstrap-tenant.ps1 -TenantName `"$TenantName`" -ExistingAppId `"$($apiApp.AppId)`" -RemoveLegacyCredentials" -ForegroundColor Yellow
}
if (-not $o365Roles.Count) {
    Write-Host ""
    Write-Host "  ActivityFeed.Read was not added. Add it to '$IngestionAppDisplayName' manually:" -ForegroundColor Yellow
    Write-Host "  API permissions > Add > APIs my organisation uses > Office 365 Management APIs > Application > ActivityFeed.Read, then grant admin consent." -ForegroundColor Yellow
}

Disconnect-MgGraph | Out-Null
