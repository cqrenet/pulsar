# Production Deployment Guide

## Overview

PULSAR runs as three Docker containers orchestrated by Docker Compose:

- **backend** — FastAPI application (Gunicorn with one Uvicorn worker), binds to `127.0.0.1:8000`, read-only root filesystem, no Linux capabilities
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
   export PULSAR_VERSION=v3.1.4  # or see VERSION
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
   cp .env.db.example .env.db && chmod 600 .env .env.db
   # Paste the lines printed by the bootstrap script into .env.
   # Set MONGO_APP_PASSWORD and REDIS_PASSWORD in .env, and MONGO_INITDB_ROOT_PASSWORD in .env.db
   # (openssl rand -hex 32 each). The root password lives only in .env.db: the backend never sees it.
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

## Upgrading from 2.x

3.0.0 changes defaults so a misconfigured install fails instead of running open. Check these before `docker compose pull && up -d`:

1. **Authentication.** PULSAR refuses to start with `AUTH_ENABLED=false`. Set `AUTH_ENABLED=true` (with `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_SCOPE`). For a throwaway development instance only, set `ALLOW_UNAUTHENTICATED=true`.
2. **CORS.** `CORS_ORIGINS` now defaults to empty (same-origin only). The UI needs no setting; set it only for a browser app on another origin.
3. **MongoDB root credentials** (provided compose files only). Create `.env.db` from `.env.db.example` with `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` (the values of your old `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD`), then delete the `MONGO_ROOT_*` lines from `.env`. The backend container no longer receives them. If you use your own compose file, make sure only the mongo service reads the root credentials.
4. **Backend container.** `docker-compose.prod.yml` now runs the backend read-only with no capabilities. If you copied the older file, add `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]` and `HOME=/tmp` to the backend service.
5. **Audit queries.** Audit-trail `action` values are semantic names since 2.0.0; nothing further changes in 3.0.0.

Then pull the new image and start it. Check `/health`, sign in, and look at `/api/source-health`.

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
   - move the MongoDB root credentials out of `.env` into a new `.env.db` (`cp .env.db.example .env.db`): `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` become `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD`. The backend must never receive them.
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

Read the release's changelog entry and its upgrade section first.

```bash
export PULSAR_VERSION=v3.x.x
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
    - pulsar-edge
    - pulsar-public
```

Put `cert.pem` and `key.pem` in `nginx/ssl/`. The configuration serves HTTPS only (port 80 redirects), limits request bodies to 1 MiB, and replaces any client-supplied `X-Forwarded-For`. Set `TRUSTED_PROXIES` to the nginx container's address.

### Azure Application Gateway / Container Apps ingress

For Azure-native deployments, TLS and routing are handled by the platform. No additional proxy configuration is needed — see the Azure-native deployment section below.

## Security hardening checklist

The full checklist is in [docs/security/operations.md](docs/security/operations.md#6-hardening-checklist-for-a-new-deployment),
and the risks it addresses are in [docs/security/known-issues.md](docs/security/known-issues.md). Do not skip these:

- [ ] `AUTH_ENABLED=true` with `AUTH_SCOPE`, `AUTH_ALLOWED_ROLES` and `AUTH_ADMIN_ROLES`; app roles assigned in Entra
- [ ] `TRUSTED_PROXIES` set to your reverse proxy's exact address. The default trusts every private address, which lets
      any client that reaches the backend directly from a private network choose its own client IP (KI-04)
- [ ] `METRICS_ALLOWED_IPS` narrowed to your Prometheus server
- [ ] The reverse proxy limits request bodies and sets, rather than appends, `X-Forwarded-For`
- [ ] `secrets/pulsar-ingestion.pem` owned by uid 10001, mode 400, rotated before it expires
- [ ] `RETENTION_DAYS` and an audit-trail retention period decided; backups off the host

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

> **Security differences from the Docker Compose deployment** ([KI-13](docs/security/known-issues.md#ki-13-azure-path-without-the-least-privilege-database-user---medium-open)): the app connects to MongoDB as `root` (or the Cosmos DB admin), so the restricted `pulsar` user and the insert-only audit trail do not apply, and the container hardening in `docker-compose.prod.yml` is not reproduced. This path has been reviewed but not tested end to end against 3.x.

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

Cosmos DB's MongoDB-compatible API works with PULSAR without code changes — only `MONGO_URI` needs updating. The `deploy/mongo-init` user script does not apply to Cosmos DB, so the app runs with the account you put in the URI; use one with the least rights Cosmos DB allows. Use a vCore cluster (M30+) for production; the connection string requires `retrywrites=false` and `tls=true`:

```
MONGO_URI=mongodb+srv://admin:PASSWORD@cluster.mongocluster.cosmos.azure.com/pulsar?tls=true&retrywrites=false&maxIdleTimeMS=120000
```

PULSAR calls `setup_indexes()` at startup — no manual index setup is needed. See [DEPLOY-AZURE.md § Phase 3](DEPLOY-AZURE.md) for migration steps from a self-hosted instance and known behavioural differences.

See [`DEPLOY-AZURE.md`](DEPLOY-AZURE.md) for the full Azure Container Apps guide, including Cosmos DB setup, migration steps, and custom domain configuration.

## Rollback

Release tags are immutable, so a previous version is always available. Check the changelog for configuration changes
between the two versions before rolling back across a major release.

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
- **CORS issues** — the UI is same-origin and needs no setting. For a browser app on another origin, set `CORS_ORIGINS` to its exact origin. A wildcard with auth enabled disables credentials.
- **Rate limiting 429s** — check Valkey connectivity: when Valkey is unreachable every rate-limited request gets 429. Behind a proxy, check `TRUSTED_PROXIES`, or all clients share the proxy's budget.
- **SIEM not forwarding** — verify `SIEM_WEBHOOK_URL` uses HTTPS and the domain is in `SIEM_ALLOWED_DOMAINS`.
