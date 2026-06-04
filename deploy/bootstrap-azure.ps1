#requires -Version 5.1
<#
.SYNOPSIS
    One-shot provisioning of the Azure infrastructure required to run PULSAR on
    Azure Container Apps.

.DESCRIPTION
    Creates (or updates) the following Azure resources in a single run:

        Resource group
        Azure Container Apps environment
        Azure Cache for Redis (Basic C0, TLS-only)
        Azure Key Vault (RBAC mode)
        User-assigned managed identity + Key Vault Secrets User role
        PULSAR backend Container App

    Secrets (connection strings, Entra credentials) are stored in Key Vault and
    surfaced to the container via managed identity secret refs — no plaintext
    environment variables.

    RUN THIS AFTER bootstrap-tenant.ps1
    bootstrap-tenant.ps1 produces the Entra values (TENANT_ID, CLIENT_ID,
    CLIENT_SECRET, AUTH_*, MCP_*) that this script expects as parameters.
    Run it first and have its output ready.

    DATABASE
    The script provisions the PULSAR container pointing at a MongoDB connection
    string you supply via -MongoUri.  Use this for:

        Containerised MongoDB in ACA     Pass the internal ACA FQDN
                                         mongodb://root:PASSWORD@pulsar-mongo/pulsar
                                         (see the deployment guide, Phase 2)

        Cosmos DB for MongoDB (vCore)    Pass the mongodb+srv:// connection string
                                         from the Cosmos portal
                                         (see the deployment guide, Phase 3)

    IMAGE VERSION
    By default the script deploys ghcr.io/cqrenet/pulsar:latest.
    Override with -ImageTag to pin to a specific release, e.g. "1.2.0".

.PARAMETER ResourceGroup
    Name of the Azure resource group to create or reuse.  Default: rg-pulsar.

.PARAMETER Location
    Azure region.  Default: westeurope.

.PARAMETER EnvironmentName
    Name of the Container Apps environment.  Default: cae-pulsar.

.PARAMETER RedisName
    Name of the Azure Cache for Redis instance.  Default: pulsar-redis.

.PARAMETER KeyVaultName
    Name of the Key Vault.  Must be globally unique (3-24 chars, alphanumeric + hyphens).

.PARAMETER IdentityName
    Name of the user-assigned managed identity.  Default: id-pulsar.

.PARAMETER ContainerAppName
    Name of the backend Container App.  Default: pulsar-backend.

.PARAMETER ImageTag
    PULSAR image tag.  Default: latest.

.PARAMETER TenantId
    Entra tenant ID (from bootstrap-tenant.ps1 output).

.PARAMETER ClientId
    Entra app / client ID (from bootstrap-tenant.ps1 output).

.PARAMETER ClientSecret
    Entra client secret (from bootstrap-tenant.ps1 output).

.PARAMETER AuthTenantId
    AUTH_TENANT_ID — usually the same as TenantId.

.PARAMETER AuthClientId
    AUTH_CLIENT_ID — usually the same as ClientId.

.PARAMETER AuthScope
    AUTH_SCOPE, e.g. api://<clientId>/user_impersonation.

.PARAMETER McpClientId
    MCP_CLIENT_ID — usually the same as ClientId.

.PARAMETER McpApiKey
    MCP_API_KEY (from bootstrap-tenant.ps1 output).

.PARAMETER McpAllowedHosts
    Comma-separated public hostname(s) for PULSAR, e.g. pulsar.contoso.com.
    Required for the MCP SSE transport to accept requests from that hostname.

.PARAMETER MongoUri
    MongoDB connection string.  Required.
    For containerised Mongo: mongodb://root:PASSWORD@pulsar-mongo/pulsar
    For Cosmos DB vCore:     mongodb+srv://admin:PASSWORD@cluster.mongocluster.cosmos.azure.com/pulsar?tls=true&retrywrites=false

.PARAMETER EnablePeriodicFetch
    Set ENABLE_PERIODIC_FETCH.  Default: true.

.PARAMETER FetchIntervalMinutes
    Set FETCH_INTERVAL_MINUTES.  Default: 60.

.PARAMETER RetentionDays
    Set RETENTION_DAYS (0 = disabled).  Default: 0.

.EXAMPLE
    # Minimal — provision everything with defaults; Cosmos DB as the database
    .\bootstrap-azure.ps1 `
      -KeyVaultName      "kv-pulsar-contoso" `
      -TenantId          "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
      -ClientId          "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy" `
      -ClientSecret      "your-client-secret" `
      -AuthTenantId      "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
      -AuthClientId      "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy" `
      -AuthScope         "api://yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy/user_impersonation" `
      -McpClientId       "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy" `
      -McpApiKey         "your-mcp-api-key" `
      -McpAllowedHosts   "pulsar.contoso.com" `
      -MongoUri          "mongodb+srv://admin:PASSWORD@pulsar-cosmos.mongocluster.cosmos.azure.com/pulsar?tls=true&retrywrites=false"

.EXAMPLE
    # Pin a specific image version and use a non-default region
    .\bootstrap-azure.ps1 `
      -Location          "northeurope" `
      -KeyVaultName      "kv-pulsar-contoso" `
      -ImageTag          "1.2.0" `
      -TenantId          "..." `
      ... (other params)
#>
[CmdletBinding()]
param (
    [string]$ResourceGroup       = "rg-pulsar",
    [string]$Location            = "westeurope",
    [string]$EnvironmentName     = "cae-pulsar",
    [string]$RedisName           = "pulsar-redis",

    [Parameter(Mandatory = $true)]
    [string]$KeyVaultName,

    [string]$IdentityName        = "id-pulsar",
    [string]$ContainerAppName    = "pulsar-backend",
    [string]$ImageTag            = "latest",

    [Parameter(Mandatory = $true)]
    [string]$TenantId,

    [Parameter(Mandatory = $true)]
    [string]$ClientId,

    [Parameter(Mandatory = $true)]
    [string]$ClientSecret,

    [Parameter(Mandatory = $true)]
    [string]$AuthTenantId,

    [Parameter(Mandatory = $true)]
    [string]$AuthClientId,

    [Parameter(Mandatory = $true)]
    [string]$AuthScope,

    [Parameter(Mandatory = $true)]
    [string]$McpClientId,

    [Parameter(Mandatory = $true)]
    [string]$McpApiKey,

    [Parameter(Mandatory = $true)]
    [string]$McpAllowedHosts,

    [Parameter(Mandatory = $true)]
    [string]$MongoUri,

    [bool]$EnablePeriodicFetch   = $true,
    [int]$FetchIntervalMinutes   = 60,
    [int]$RetentionDays          = 0
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Step {
    param ([string]$Message)
    Write-Host ""
    Write-Host ">>> $Message" -ForegroundColor Cyan
}

function OK {
    param ([string]$Message)
    Write-Host "    $Message" -ForegroundColor Green
}

function Warn {
    param ([string]$Message)
    Write-Host "    $Message" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Preflight: Azure CLI
# ---------------------------------------------------------------------------

Step "Checking Azure CLI"
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI not found. Install from https://aka.ms/installazurecli and run 'az login'."
}
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    throw "Not logged in to Azure CLI. Run 'az login' first."
}
OK "Logged in as: $($account.user.name) | Subscription: $($account.name) ($($account.id))"

# ---------------------------------------------------------------------------
# Resource group
# ---------------------------------------------------------------------------

Step "Resource group: $ResourceGroup"
$rgExists = az group exists --name $ResourceGroup | ConvertFrom-Json
if ($rgExists) {
    Warn "Already exists — skipping."
} else {
    az group create --name $ResourceGroup --location $Location | Out-Null
    OK "Created."
}

# ---------------------------------------------------------------------------
# Container Apps environment
# ---------------------------------------------------------------------------

Step "Container Apps environment: $EnvironmentName"
$envExists = az containerapp env show --name $EnvironmentName --resource-group $ResourceGroup 2>$null
if ($envExists) {
    Warn "Already exists — skipping."
} else {
    az containerapp env create `
        --name $EnvironmentName `
        --resource-group $ResourceGroup `
        --location $Location | Out-Null
    OK "Created."
}

# ---------------------------------------------------------------------------
# Azure Cache for Redis
# ---------------------------------------------------------------------------

Step "Azure Cache for Redis: $RedisName"
$redisExists = az redis show --name $RedisName --resource-group $ResourceGroup 2>$null
if ($redisExists) {
    Warn "Already exists — skipping creation."
} else {
    Write-Host "    Provisioning Redis (this takes 10-20 minutes)..." -ForegroundColor DarkGray
    az redis create `
        --name $RedisName `
        --resource-group $ResourceGroup `
        --location $Location `
        --sku Basic `
        --vm-size C0 | Out-Null
    OK "Created."
}

$redisPrimaryKey = az redis list-keys `
    --name $RedisName `
    --resource-group $ResourceGroup `
    --query primaryKey -o tsv

$redisHost = "$RedisName.redis.cache.windows.net"
$redisUrl  = "rediss://:${redisPrimaryKey}@${redisHost}:6380/0"
OK "Redis URL: rediss://:***@${redisHost}:6380/0"

# ---------------------------------------------------------------------------
# Key Vault
# ---------------------------------------------------------------------------

Step "Key Vault: $KeyVaultName"
$kvExists = az keyvault show --name $KeyVaultName --resource-group $ResourceGroup 2>$null
if ($kvExists) {
    Warn "Already exists — skipping creation."
} else {
    az keyvault create `
        --name $KeyVaultName `
        --resource-group $ResourceGroup `
        --location $Location `
        --enable-rbac-authorization true | Out-Null
    OK "Created."
}

$kvId = az keyvault show --name $KeyVaultName --resource-group $ResourceGroup --query id -o tsv

# Store secrets
Step "Storing secrets in Key Vault"
$secrets = @{
    "tenant-id"     = $TenantId
    "client-id"     = $ClientId
    "client-secret" = $ClientSecret
    "auth-tenant-id"= $AuthTenantId
    "auth-client-id"= $AuthClientId
    "auth-scope"    = $AuthScope
    "mcp-client-id" = $McpClientId
    "mcp-api-key"   = $McpApiKey
    "redis-url"     = $redisUrl
    "mongo-uri"     = $MongoUri
}

foreach ($name in $secrets.Keys) {
    az keyvault secret set `
        --vault-name $KeyVaultName `
        --name $name `
        --value $secrets[$name] | Out-Null
    OK "  $name"
}

# ---------------------------------------------------------------------------
# Managed identity
# ---------------------------------------------------------------------------

Step "Managed identity: $IdentityName"
$idExists = az identity show --name $IdentityName --resource-group $ResourceGroup 2>$null
if ($idExists) {
    Warn "Already exists — skipping creation."
} else {
    az identity create --name $IdentityName --resource-group $ResourceGroup | Out-Null
    OK "Created."
}

$identityId       = az identity show --name $IdentityName --resource-group $ResourceGroup --query id -o tsv
$identityClientId = az identity show --name $IdentityName --resource-group $ResourceGroup --query clientId -o tsv
$identityPrincipalId = az identity show --name $IdentityName --resource-group $ResourceGroup --query principalId -o tsv

# Grant Key Vault Secrets User
Step "Granting Key Vault Secrets User to managed identity"
$roleExists = az role assignment list `
    --role "Key Vault Secrets User" `
    --assignee $identityPrincipalId `
    --scope $kvId `
    --query "[0].id" -o tsv 2>$null

if ($roleExists) {
    Warn "Role already assigned — skipping."
} else {
    az role assignment create `
        --role "Key Vault Secrets User" `
        --assignee $identityPrincipalId `
        --scope $kvId | Out-Null
    OK "Role assigned."
}

# ---------------------------------------------------------------------------
# Container App
# ---------------------------------------------------------------------------

function KvRef {
    param ([string]$SecretName)
    return "keyvaultref:https://${KeyVaultName}.vault.azure.net/secrets/${SecretName},identityref:${identityId}"
}

Step "Container App: $ContainerAppName"
$caExists = az containerapp show --name $ContainerAppName --resource-group $ResourceGroup 2>$null
if ($caExists) {
    Warn "Already exists — updating image and environment."
    az containerapp update `
        --name $ContainerAppName `
        --resource-group $ResourceGroup `
        --image "ghcr.io/cqrenet/pulsar:$ImageTag" | Out-Null
    OK "Image updated."
} else {
    $periodicFetchStr = if ($EnablePeriodicFetch) { "true" } else { "false" }

    az containerapp create `
        --name $ContainerAppName `
        --resource-group $ResourceGroup `
        --environment $EnvironmentName `
        --image "ghcr.io/cqrenet/pulsar:$ImageTag" `
        --cpu 0.5 --memory 1.0Gi `
        --min-replicas 1 --max-replicas 1 `
        --ingress external --target-port 8000 `
        --user-assigned $identityId `
        --secrets `
            mongo-uri=$(KvRef "mongo-uri") `
            redis-url=$(KvRef "redis-url") `
            tenant-id=$(KvRef "tenant-id") `
            client-id=$(KvRef "client-id") `
            client-secret=$(KvRef "client-secret") `
            auth-tenant-id=$(KvRef "auth-tenant-id") `
            auth-client-id=$(KvRef "auth-client-id") `
            auth-scope=$(KvRef "auth-scope") `
            mcp-client-id=$(KvRef "mcp-client-id") `
            mcp-api-key=$(KvRef "mcp-api-key") `
        --env-vars `
            MONGO_URI=secretref:mongo-uri `
            REDIS_URL=secretref:redis-url `
            TENANT_ID=secretref:tenant-id `
            CLIENT_ID=secretref:client-id `
            CLIENT_SECRET=secretref:client-secret `
            AUTH_ENABLED=true `
            AUTH_TENANT_ID=secretref:auth-tenant-id `
            AUTH_CLIENT_ID=secretref:auth-client-id `
            AUTH_SCOPE=secretref:auth-scope `
            MCP_CLIENT_ID=secretref:mcp-client-id `
            MCP_API_KEY=secretref:mcp-api-key `
            MCP_ALLOWED_HOSTS="$McpAllowedHosts" `
            ENABLE_PERIODIC_FETCH="$periodicFetchStr" `
            FETCH_INTERVAL_MINUTES="$FetchIntervalMinutes" `
            RETENTION_DAYS="$RetentionDays" `
            CORS_ORIGINS="https://$McpAllowedHosts" `
            DOCS_ENABLED=false `
            PYTHONUNBUFFERED=1 | Out-Null
    OK "Container App created."
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

$fqdn = az containerapp show `
    --name $ContainerAppName `
    --resource-group $ResourceGroup `
    --query "properties.configuration.ingress.fqdn" -o tsv

Write-Host ""
Write-Host "=== Bootstrap complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Resource group:   $ResourceGroup"
Write-Host "ACA environment:  $EnvironmentName"
Write-Host "Redis:            $redisHost"
Write-Host "Key Vault:        $KeyVaultName"
Write-Host "Identity:         $IdentityName"
Write-Host "Container App:    $ContainerAppName"
Write-Host ""
Write-Host "Default FQDN:     https://$fqdn"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Validate the deployment:"
Write-Host "     curl https://$fqdn/health"
Write-Host ""
Write-Host "  2. Bind a custom domain and managed TLS certificate:"
Write-Host "     - Add a CNAME at your DNS provider: $McpAllowedHosts → $fqdn"
Write-Host "     - Run:"
Write-Host "       az containerapp hostname add --name $ContainerAppName --resource-group $ResourceGroup --hostname $McpAllowedHosts"
Write-Host "       az containerapp ssl upload --name $ContainerAppName --resource-group $ResourceGroup --hostname $McpAllowedHosts --certificate-type managed"
Write-Host ""
Write-Host "  3. Update MCP_ALLOWED_HOSTS if you add further hostnames:"
Write-Host "     az containerapp update -n $ContainerAppName -g $ResourceGroup --set-env-vars MCP_ALLOWED_HOSTS=<comma-separated>"
Write-Host ""
Write-Host "  4. To rotate a secret later, update it in Key Vault and restart the Container App:"
Write-Host "     az keyvault secret set --vault-name $KeyVaultName --name <secret-name> --value <new-value>"
Write-Host "     az containerapp revision restart -n $ContainerAppName -g $ResourceGroup --revision-name (az containerapp revision list -n $ContainerAppName -g $ResourceGroup --query '[0].name' -o tsv)"
