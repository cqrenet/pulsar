# Production Deployment Guide

## Overview

PULSAR runs as three Docker containers orchestrated by Docker Compose:

- **backend** — FastAPI application (Gunicorn + Uvicorn workers), binds to `127.0.0.1:8000`
- **mongo** — MongoDB data store (internal network only, never exposed)
- **redis** — Valkey/Redis cache (internal network only, never exposed)

PULSAR handles sensitive audit data and admin credentials. It must always be accessed through a reverse proxy — never expose the backend port directly to the network. In production, your organisation's existing reverse proxy (nginx, Traefik, Caddy, Azure Application Gateway, etc.) should handle TLS termination and forward to `127.0.0.1:8000`. A standalone nginx option is documented below for deployments without an existing proxy.

## Prerequisites

- Docker Engine 24+ and Docker Compose plugin
- A reverse proxy configured to forward HTTPS traffic to `127.0.0.1:8000`
- A valid `.env` file at the repo root (see `.env.example`)
- The ingestion certificate in `secrets/pulsar-ingestion.pem` (step 2 below)
- The Entra app registrations — `deploy/bootstrap-tenant.ps1` creates both and prints the exact `.env` lines (step 3 below). Upgrading an existing install? Follow [Upgrading from 1.4.x](#upgrading-from-14x) instead.

## Quick start

1. **Pull the latest release image**

   ```bash
   export PULSAR_VERSION=v2.0.0  # or see VERSION
   docker compose -f docker-compose.prod.yml pull
   ```

2. **Create the ingestion key and certificate on this server** (the private key never leaves it)

   ```bash
   mkdir -p secrets && cd secrets
   openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=PULSAR Ingestion" \
       -keyout key.pem -out pulsar-ingestion.crt
   cat key.pem pulsar-ingestion.crt > pulsar-ingestion.pem && rm key.pem
   sudo chown 10001:10001 pulsar-ingestion.pem && sudo chmod 400 pulsar-ingestion.pem   # 10001 = container user
   cd ..
   ```

   Copy only `secrets/pulsar-ingestion.crt` (public) to the workstation where you run step 3.

3. **Create the Entra app registrations** (PowerShell 7, an account that can grant admin consent)

   ```powershell
   .\deploy\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" `
       -PublicUrl "https://pulsar.contoso.com" -PublicCertificatePath .\pulsar-ingestion.crt
   ```

   Then assign the `PulsarReader` / `PulsarAdmin` / `PulsarPrivacyReader` roles to users or groups (Enterprise applications → PULSAR → Users and groups). Only assigned users can sign in.

4. **Copy and edit environment variables**

   ```bash
   cp .env.example .env
   # Paste the lines printed by the bootstrap script.
   # Set MONGO_ROOT_PASSWORD, MONGO_APP_PASSWORD and REDIS_PASSWORD (openssl rand -hex 32 each).
   # If using the MCP SSE endpoint behind a reverse proxy, also set MCP_ALLOWED_HOSTS
   ```

5. **Deploy**

   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```

6. **Verify**

   ```bash
   curl http://localhost/health
   ```

## Upgrading from 1.4.x

This release splits the Entra app registration in two, replaces the client secret with a certificate, makes the app use a least-privilege MongoDB user, and password-protects Valkey. Expect a few minutes of downtime. Users and MCP clients keep working without changes: your existing app keeps its client ID and becomes the API app.

> **Access changes:** only users assigned a PULSAR app role can sign in afterwards. Assign roles (step 4) before you restart.

1. **Update the checkout** on the server: `git pull` (or check out the release tag).
2. **Create the ingestion key and certificate** on the server — Quick start step 2.
3. **Run the bootstrap against your existing app** (its ID is your current `CLIENT_ID`):

   ```powershell
   .\deploy\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -ExistingAppId "<current CLIENT_ID>" `
       -PublicUrl "https://pulsar.contoso.com" -PublicCertificatePath .\pulsar-ingestion.crt
   ```

4. **Assign app roles** to everyone who uses PULSAR (Enterprise applications → PULSAR → Users and groups).
5. **Edit `.env`**:
   - set `CLIENT_ID` to the new **ingestion** app ID and delete `CLIENT_SECRET`;
   - add the `AUTH_*`, `PRIVACY_SERVICE_ROLES` and `MCP_CLIENT_ID` lines the script printed (`AUTH_CLIENT_ID` is your old `CLIENT_ID`);
   - add `MONGO_APP_PASSWORD` and `REDIS_PASSWORD` (`openssl rand -hex 32` each); delete any `MONGO_URI` / `REDIS_URL` lines.
6. **Create the MongoDB app user** on the existing database (init scripts only run on an empty volume). This also copies the audit trail into its own database:

   ```bash
   docker compose -f docker-compose.prod.yml up -d mongo
   docker compose -f docker-compose.prod.yml exec mongo sh -c \
     'mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" /docker-entrypoint-initdb.d/01-pulsar-user.js'
   ```

7. **Start the new release**: `export PULSAR_VERSION=<new tag>`, then `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`.
8. **Verify**: sign in, press **Fetch**, check the source-health dots turn green, and run one MCP query.
9. **Clean up** once ingestion works:

   ```powershell
   .\deploy\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -ExistingAppId "<API app ID>" -RemoveLegacyCredentials
   ```

   ```bash
   docker compose -f docker-compose.prod.yml exec mongo sh -c \
     'mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --eval "db.getSiblingDB(\"micro_soc\").pulsar_audit.drop()"'
   ```

**Rotating the ingestion certificate:** repeat Quick start step 2 under a new file name, run the bootstrap with `-PublicCertificatePath` for the new `.crt`, move the new `.pem` into place, and restart the backend. Fetches fail between the upload and the restart.

## Updating to a new release

```bash
export PULSAR_VERSION=v1.x.x
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

## Reverse proxy

PULSAR binds to `127.0.0.1:8000` and expects your reverse proxy to handle TLS and forward traffic to it. Configure your proxy to:

- Terminate TLS (HTTPS)
- Forward to `http://127.0.0.1:8000`
- Pass `X-Forwarded-For` and `X-Forwarded-Proto` headers

### Traefik (Docker label-based)

Add labels to the backend service in your compose override:

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.pulsar.rule=Host(`pulsar.your-domain.com`)"
  - "traefik.http.routers.pulsar.entrypoints=websecure"
  - "traefik.http.routers.pulsar.tls.certresolver=letsencrypt"
  - "traefik.http.services.pulsar.loadbalancer.server.port=8000"
```

### Caddy

```
pulsar.your-domain.com {
    reverse_proxy localhost:8000
}
```

Caddy handles TLS automatically via Let's Encrypt.

### Standalone nginx (no existing proxy)

If you don't have a central reverse proxy, the `nginx/` directory contains a basic configuration. Add an nginx service to your compose file:

```yaml
nginx:
  image: nginx:alpine
  container_name: pulsar-nginx
  restart: always
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    - ./nginx/ssl:/etc/nginx/ssl:ro
  depends_on:
    backend:
      condition: service_healthy
  networks:
    - pulsar-internal
    - pulsar-public
```

Place your TLS certificates in `nginx/ssl/` and uncomment the HTTPS server block in `nginx/nginx.conf`.

### Azure Application Gateway / Container Apps ingress

For Azure-native deployments, TLS and routing are handled by the platform. No additional proxy configuration is needed — see the Azure-native deployment section below.

## Security hardening checklist

- [ ] Set `AUTH_ENABLED=true` and configure `AUTH_ALLOWED_ROLES` or `AUTH_ALLOWED_GROUPS`
- [ ] Assign PULSAR app roles in Entra (assignment is required for sign-in)
- [ ] Keep `secrets/pulsar-ingestion.pem` owned by uid 10001 with mode 400; rotate it before it expires
- [ ] Set `AUTH_SCOPE` (e.g. `api://<client-id>/user_impersonation`) — ID tokens are not accepted
- [ ] Set `AUTH_ADMIN_ROLES` so only admins can change alert rules or trigger a fetch
- [ ] Set `TRUSTED_PROXIES` to your reverse proxy if it is not on a private network
- [ ] Set explicit `CORS_ORIGINS` — never use `*` with auth enabled
- [ ] Set `DOCS_ENABLED=false` (default) to hide OpenAPI docs
- [ ] Configure `WEBHOOK_CLIENT_SECRET` — Graph webhook notifications are rejected without it
- [ ] Set `SIEM_ALLOWED_DOMAINS` if using SIEM forwarding
- [ ] Review `METRICS_ALLOWED_IPS` — defaults to private networks only
- [ ] Set `MCP_ALLOWED_HOSTS` if exposing the MCP SSE endpoint behind a reverse proxy

## Azure Key Vault (optional)

To store secrets in Azure Key Vault instead of `.env`:

1. Create a Key Vault and add these secrets:
   - `pulsar-mongo-uri` → `MONGO_URI`
   - `pulsar-webhook-client-secret` → `WEBHOOK_CLIENT_SECRET`

2. Add `azure-identity` and `azure-keyvault-secrets` to `backend/requirements.in` and regenerate the lock file (command at the top of that file)

3. Set `AZURE_KEY_VAULT_NAME=your-keyvault-name` in `.env`

4. Ensure the container identity has `Get` permission on secrets (managed identity or service principal)

## Azure-native deployment

PULSAR runs natively on Azure Container Apps with Azure Cache for Redis and either a containerised MongoDB or Cosmos DB for MongoDB as the database. Secrets are managed via Key Vault and a managed identity — no credentials in environment variables.

For the full step-by-step guide, including the Cosmos DB migration path and custom domain setup, see **[DEPLOY-AZURE.md](DEPLOY-AZURE.md)**.

### Quick start (automated)

Run `deploy/bootstrap-tenant.ps1` first to create the Entra app registrations, then provision the full Azure stack in one shot:

```powershell
.\deploy\bootstrap-azure.ps1 `
  -KeyVaultName    "kv-pulsar-contoso" `
  -TenantId        "<TENANT_ID>" `
  -ClientId        "<ingestion app CLIENT_ID>" `
  -CertificatePemPath ".\pulsar-ingestion.pem" `
  -AuthTenantId    "<TENANT_ID>" `
  -AuthClientId    "<API app ID>" `
  -AuthScope       "api://<API app ID>/user_impersonation" `
  -McpClientId     "<API app ID>" `
  -McpApiKey       "<MCP_API_KEY>" `
  -McpAllowedHosts "pulsar.yourdomain.com" `
  -MongoUri        "<MONGO_URI>"
```

The script creates the Container Apps environment, Azure Cache for Redis, Key Vault (with all secrets stored as managed identity secret refs), and the PULSAR Container App. Pass a Cosmos DB `mongodb+srv://` connection string to `-MongoUri` for a fully managed database, or a local MongoDB URI to use a containerised instance (see [DEPLOY-AZURE.md](DEPLOY-AZURE.md) for both options).

### Cosmos DB for MongoDB

Cosmos DB's MongoDB-compatible API works with PULSAR without code changes — only `MONGO_URI` needs updating. Use a vCore cluster (M30+) for production; the connection string requires `retrywrites=false` and `tls=true`:

```
MONGO_URI=mongodb+srv://admin:PASSWORD@cluster.mongocluster.cosmos.azure.com/pulsar?tls=true&retrywrites=false&maxIdleTimeMS=120000
```

PULSAR calls `setup_indexes()` at startup — no manual index setup is needed. See [DEPLOY-AZURE.md § Phase 3](DEPLOY-AZURE.md) for migration steps from a self-hosted instance and known behavioural differences.

See [`DEPLOY-AZURE.md`](DEPLOY-AZURE.md) for the full Azure Container Apps guide, including Cosmos DB setup, migration steps, and custom domain configuration.

## Rollback

```bash
export PULSAR_VERSION=v1.x.x   # previous version
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

## Monitoring

- Health: `http://your-host/health`
- Prometheus metrics: `http://your-host/metrics` (IP-restricted by default)
- Logs:

  ```bash
  docker compose -f docker-compose.prod.yml logs -f backend
  docker compose -f docker-compose.prod.yml logs -f nginx
  ```

## Troubleshooting

- **Auth warning in logs** — "AUTH_ENABLED is true but no AUTH_ALLOWED_ROLES or AUTH_ALLOWED_GROUPS configured": set these to restrict access.
- **CORS issues** — set `CORS_ORIGINS` to your exact frontend origin. Wildcard with auth enabled disables credentials.
- **Rate limiting 429s** — check Redis/Valkey connectivity. The rate limiter fails closed when Redis is unreachable.
- **SIEM not forwarding** — verify `SIEM_WEBHOOK_URL` uses HTTPS and the domain is in `SIEM_ALLOWED_DOMAINS`.
