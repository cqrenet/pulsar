# PULSAR Azure Deployment Guide

Deploy PULSAR on Azure Container Apps alongside an existing ASTRAL deployment.  
This guide covers the full stack: the PULSAR backend container, managed Redis, and two database paths — **MongoDB-in-ACA** (default) and **Cosmos DB for MongoDB** (recommended for production Azure deployments).

---

## Architecture overview

```
Internet
  └── ACA Ingress (HTTPS, managed cert)
        └── pulsar-backend  (Container App, single worker)
              ├── Azure Cache for Redis
              └── MongoDB  ← either containerised in ACA  (quick start)
                           └── or Cosmos DB vCore         (recommended)
```

Secrets are stored in Azure Key Vault and surfaced to the container via managed identity — no credentials in environment variables or pipeline variables.

---

## Prerequisites

- Azure CLI installed and authenticated (`az login`)
- A resource group and region chosen (examples use `rg-pulsar` / `westeurope`)
- Docker image available at `ghcr.io/cqrenet/pulsar:latest` (or your tagged version)
- An Entra app registration for PULSAR (same tenant as ASTRAL, or a new one — see `.env.example` for the variables it produces)

---

## Automated provisioning

Phases 1–4 can be run in one shot using the bootstrap script in `deploy/bootstrap-azure.ps1`.  Run `bootstrap-tenant.ps1` first to obtain the Entra values, then:

```powershell
.\deploy\bootstrap-azure.ps1 `
  -KeyVaultName    "kv-pulsar-contoso" `
  -TenantId        "<TENANT_ID>" `
  -ClientId        "<CLIENT_ID>" `
  -ClientSecret    "<CLIENT_SECRET>" `
  -AuthTenantId    "<TENANT_ID>" `
  -AuthClientId    "<CLIENT_ID>" `
  -AuthScope       "api://<CLIENT_ID>/user_impersonation" `
  -McpClientId     "<CLIENT_ID>" `
  -McpApiKey       "<MCP_API_KEY>" `
  -McpAllowedHosts "pulsar.yourdomain.com" `
  -MongoUri        "<MONGO_URI>"
```

The manual steps below explain what the script does and cover options (Cosmos DB, custom region, image pinning) that require additional parameters.

---

## Phase 1: Core infrastructure

### 1.1 Create the Container Apps environment

```bash
az group create -n rg-pulsar -l westeurope

az containerapp env create \
  --name cae-pulsar \
  --resource-group rg-pulsar \
  --location westeurope
```

### 1.2 Provision Azure Cache for Redis

PULSAR uses Redis (Valkey) for rate limiting.  The Basic C0 tier is sufficient for most deployments.

```bash
az redis create \
  --name pulsar-redis \
  --resource-group rg-pulsar \
  --location westeurope \
  --sku Basic \
  --vm-size C0
```

Note the primary connection string once provisioning completes:

```bash
az redis list-keys --name pulsar-redis --resource-group rg-pulsar \
  --query primaryKey -o tsv
```

The `REDIS_URL` for the backend will be:
```
rediss://:PRIMARY_KEY@pulsar-redis.redis.cache.windows.net:6380/0
```
(Note `rediss://` — TLS is required on Azure Cache.)

### 1.3 Provision Key Vault

```bash
az keyvault create \
  --name kv-pulsar \
  --resource-group rg-pulsar \
  --location westeurope \
  --enable-rbac-authorization true
```

Store all secrets now.  Adjust values to match your `.env`:

```bash
# Entra / M365 credentials
az keyvault secret set --vault-name kv-pulsar --name tenant-id        --value "<TENANT_ID>"
az keyvault secret set --vault-name kv-pulsar --name client-id        --value "<CLIENT_ID>"
az keyvault secret set --vault-name kv-pulsar --name client-secret    --value "<CLIENT_SECRET>"

# Auth (if AUTH_ENABLED=true)
az keyvault secret set --vault-name kv-pulsar --name auth-tenant-id   --value "<AUTH_TENANT_ID>"
az keyvault secret set --vault-name kv-pulsar --name auth-client-id   --value "<AUTH_CLIENT_ID>"
az keyvault secret set --vault-name kv-pulsar --name auth-scope       --value "<AUTH_SCOPE>"

# MCP
az keyvault secret set --vault-name kv-pulsar --name mcp-api-key      --value "<MCP_API_KEY>"

# Redis
az keyvault secret set --vault-name kv-pulsar --name redis-url \
  --value "rediss://:PRIMARY_KEY@pulsar-redis.redis.cache.windows.net:6380/0"

# MongoDB URI — set after Phase 2 (containerised) or Phase 3 (Cosmos)
# az keyvault secret set --vault-name kv-pulsar --name mongo-uri --value "..."
```

---

## Phase 2: MongoDB deployment (containerised — quick start)

> **Skip to Phase 3** if you are targeting Cosmos DB from the start.  
> You can migrate later — the Cosmos DB path is a drop-in connection string swap with a few caveats documented in Phase 3.

Run MongoDB as a sidecar Container App in the same environment.  This mirrors the `docker-compose.prod.yml` approach and is suitable for evaluation or non-critical environments.

```bash
az containerapp create \
  --name pulsar-mongo \
  --resource-group rg-pulsar \
  --environment cae-pulsar \
  --image mongo:7 \
  --cpu 0.5 --memory 1.0Gi \
  --min-replicas 1 --max-replicas 1 \
  --ingress internal --target-port 27017 \
  --env-vars \
    MONGO_INITDB_ROOT_USERNAME=root \
    MONGO_INITDB_ROOT_PASSWORD=secretref:mongo-root-password
```

Add the MongoDB password to Key Vault and create the secret reference:

```bash
az keyvault secret set --vault-name kv-pulsar --name mongo-root-password --value "<STRONG_PASSWORD>"
az keyvault secret set --vault-name kv-pulsar --name mongo-uri \
  --value "mongodb://root:STRONG_PASSWORD@pulsar-mongo/pulsar"
```

> **Storage note:** Container Apps volumes are ephemeral by default.  For a containerised MongoDB, attach an Azure Files share:
>
> ```bash
> az storage account create -n pulsarmongodata -g rg-pulsar -l westeurope --sku Standard_LRS
> az containerapp env storage set \
>   --name cae-pulsar --resource-group rg-pulsar \
>   --storage-name mongo-data \
>   --azure-file-account-name pulsarmongodata \
>   --azure-file-share-name mongodata \
>   --azure-file-account-key "$(az storage account keys list -n pulsarmongodata -g rg-pulsar --query '[0].value' -o tsv)" \
>   --access-mode ReadWrite
> ```
> Then mount it at `/data/db` in the container definition.

---

## Phase 3: Cosmos DB for MongoDB (recommended for production)

Azure Cosmos DB for MongoDB (vCore) is the recommended database backend for a production Azure deployment.  It is wire-compatible with MongoDB 7, fully managed, and removes the operational burden of running a stateful container.

### 3.1 Provision a Cosmos DB vCore cluster

```bash
az cosmosdb mongocluster create \
  --cluster-name pulsar-cosmos \
  --resource-group rg-pulsar \
  --location westeurope \
  --administrator-login pulsaradmin \
  --administrator-login-password "<STRONG_PASSWORD>" \
  --shard-count 1 \
  --compute-tier M30
```

> The M30 tier (2 vCores, 8 GB RAM) is appropriate for a single-tenant PULSAR deployment.  Scale up if you are ingesting high audit log volumes.

Once the cluster is created, retrieve the connection string from the portal (**Settings → Connection strings**) or via CLI.  It will be in the form:

```
mongodb+srv://pulsaradmin:<PASSWORD>@pulsar-cosmos.mongocluster.cosmos.azure.com/?tls=true&authMechanism=SCRAM-SHA-256&retrywrites=false&maxIdleTimeMS=120000
```

Store it in Key Vault:

```bash
az keyvault secret set --vault-name kv-pulsar --name mongo-uri \
  --value "mongodb+srv://pulsaradmin:PASSWORD@pulsar-cosmos.mongocluster.cosmos.azure.com/pulsar?tls=true&authMechanism=SCRAM-SHA-256&retrywrites=false&maxIdleTimeMS=120000"
```

### 3.2 Caveats and differences from self-hosted MongoDB

| Area | Self-hosted Mongo 7 | Cosmos DB vCore |
|------|--------------------|--------------------|
| Connection string scheme | `mongodb://` | `mongodb+srv://` with `?tls=true` |
| `retrywrites` | supported | set to `false` (not supported) |
| Healthcheck (`mongosh`) | works | not available from container — remove or replace with HTTP `/health` check |
| Index creation | automatic | automatic (same driver behaviour) |
| Transactions | supported | supported on vCore (M30+) |
| Scale | manual | vCore: vertical; or switch to RU-based for serverless |

**Removing the `mongosh` healthcheck:** If you adapted the `docker-compose.prod.yml` healthcheck for the backend container, replace it with the PULSAR `/health` endpoint check (already present in the prod compose file).  Do not add a Cosmos-targeted `mongosh` healthcheck — it will fail.

### 3.3 Migrating from containerised MongoDB to Cosmos DB

If you started with Phase 2 and want to migrate:

1. **Export from the running Mongo container:**
   ```bash
   # Get the container App's replica name
   az containerapp replica list -n pulsar-mongo -g rg-pulsar --query '[0].name' -o tsv

   # Run mongodump via exec (or use a temporary sidecar)
   az containerapp exec -n pulsar-mongo -g rg-pulsar \
     --command "mongodump --uri mongodb://root:PASSWORD@localhost/pulsar --archive=/tmp/pulsar.archive"
   ```

2. **Copy the archive out** (via `az storage` or a temporary Azure Files mount).

3. **Restore into Cosmos DB:**
   ```bash
   mongorestore \
     --uri "mongodb+srv://pulsaradmin:PASSWORD@pulsar-cosmos.mongocluster.cosmos.azure.com/pulsar?tls=true&retrywrites=false" \
     --archive=pulsar.archive
   ```

4. **Update the Key Vault secret** `mongo-uri` to the Cosmos connection string.

5. **Restart the backend Container App** to pick up the new secret.

6. **Decommission** the `pulsar-mongo` Container App once the migration is confirmed.

---

## Phase 4: Deploy the PULSAR backend Container App

### 4.1 Create a managed identity and grant Key Vault access

```bash
az identity create --name id-pulsar --resource-group rg-pulsar

IDENTITY_ID=$(az identity show --name id-pulsar --resource-group rg-pulsar --query id -o tsv)
IDENTITY_CLIENT_ID=$(az identity show --name id-pulsar --resource-group rg-pulsar --query clientId -o tsv)
KV_ID=$(az keyvault show --name kv-pulsar --query id -o tsv)

az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee $IDENTITY_CLIENT_ID \
  --scope $KV_ID
```

### 4.2 Create the Container App

```bash
az containerapp create \
  --name pulsar-backend \
  --resource-group rg-pulsar \
  --environment cae-pulsar \
  --image ghcr.io/cqrenet/pulsar:latest \
  --cpu 0.5 --memory 1.0Gi \
  --min-replicas 1 --max-replicas 1 \
  --ingress external --target-port 8000 \
  --user-assigned $IDENTITY_ID \
  --secrets \
    mongo-uri=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/mongo-uri,identityref:$IDENTITY_ID \
    redis-url=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/redis-url,identityref:$IDENTITY_ID \
    tenant-id=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/tenant-id,identityref:$IDENTITY_ID \
    client-id=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/client-id,identityref:$IDENTITY_ID \
    client-secret=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/client-secret,identityref:$IDENTITY_ID \
    mcp-api-key=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/mcp-api-key,identityref:$IDENTITY_ID \
  --env-vars \
    MONGO_URI=secretref:mongo-uri \
    REDIS_URL=secretref:redis-url \
    TENANT_ID=secretref:tenant-id \
    CLIENT_ID=secretref:client-id \
    CLIENT_SECRET=secretref:client-secret \
    MCP_API_KEY=secretref:mcp-api-key \
    ENABLE_PERIODIC_FETCH=true \
    FETCH_INTERVAL_MINUTES=60 \
    AUTH_ENABLED=true \
    MCP_ALLOWED_HOSTS=pulsar.yourdomain.com \
    CORS_ORIGINS=https://pulsar.yourdomain.com \
    DOCS_ENABLED=false \
    PYTHONUNBUFFERED=1
```

Add the auth env vars if `AUTH_ENABLED=true`:

```bash
az containerapp update -n pulsar-backend -g rg-pulsar \
  --secrets \
    auth-tenant-id=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/auth-tenant-id,identityref:$IDENTITY_ID \
    auth-client-id=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/auth-client-id,identityref:$IDENTITY_ID \
    auth-scope=keyvaultref:https://kv-pulsar.vault.azure.net/secrets/auth-scope,identityref:$IDENTITY_ID \
  --env-vars \
    AUTH_TENANT_ID=secretref:auth-tenant-id \
    AUTH_CLIENT_ID=secretref:auth-client-id \
    AUTH_SCOPE=secretref:auth-scope
```

### 4.3 Configure a custom domain and managed TLS

```bash
# Get the default domain first
az containerapp show -n pulsar-backend -g rg-pulsar --query "properties.configuration.ingress.fqdn" -o tsv

# Add CNAME record at your DNS provider:
#   pulsar.yourdomain.com  →  <above FQDN>

# Then bind the custom domain (ACA will provision the certificate automatically)
az containerapp hostname add \
  --name pulsar-backend \
  --resource-group rg-pulsar \
  --hostname pulsar.yourdomain.com

az containerapp ssl upload \
  --name pulsar-backend \
  --resource-group rg-pulsar \
  --hostname pulsar.yourdomain.com \
  --certificate-type managed
```

### 4.4 Validate

```bash
curl -s https://pulsar.yourdomain.com/health | python3 -m json.tool
```

Expected response:

```json
{
  "status": "ok",
  ...
}
```

---

## Phase 5: Co-location with ASTRAL

PULSAR and ASTRAL run independently — ASTRAL uses ADO pipelines against your M365 tenant, while PULSAR is a persistent container service.  They share:

- The same **Entra tenant** (and optionally the same Key Vault if you choose to consolidate)
- Optionally the same **ACA environment** (rename `cae-pulsar` to a shared name and deploy both products into it)

No further integration is required at the infrastructure level.  AURORA, when deployed, connects to both.

---

## Summary checklist

- [ ] Resource group and ACA environment created
- [ ] Azure Cache for Redis provisioned; connection string in Key Vault
- [ ] Key Vault created; all secrets stored
- [ ] **Database**: containerised MongoDB (Phase 2) **or** Cosmos DB vCore (Phase 3)
- [ ] Managed identity created; Key Vault Secrets User role assigned
- [ ] `pulsar-backend` Container App created with all secret refs and env vars
- [ ] Custom domain bound; managed TLS certificate provisioned
- [ ] `/health` endpoint returns `200 ok`
- [ ] (If migrating from Docker Compose) `mongodump` → `mongorestore` into Cosmos completed and verified
- [ ] Containerised `pulsar-mongo` decommissioned (if Cosmos path taken)
