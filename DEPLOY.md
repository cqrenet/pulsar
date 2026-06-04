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
- An Entra app registration — run `.\deploy\bootstrap-tenant.ps1 -TenantName "<tenant>"` to create it and get the exact `.env` lines to paste. For existing deployments with a manually created app, use `.\deploy\bootstrap-mcp-auth.ps1 -TenantName "<tenant>" -ExistingAppId "<CLIENT_ID>"` to add MCP capabilities without touching the rest.

## Quick start

1. **Pull the latest release image**

   ```bash
   export PULSAR_VERSION=v1.2.2  # or see VERSION
   docker compose -f docker-compose.prod.yml pull
   ```

2. **Copy and edit environment variables**

   ```bash
   cp .env.example .env
   # Fill in TENANT_ID, CLIENT_ID, CLIENT_SECRET and any optional settings
   # If using the MCP SSE endpoint behind a reverse proxy, also set MCP_ALLOWED_HOSTS
   ```

3. **Deploy**

   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```

4. **Verify**

   ```bash
   curl http://localhost/health
   ```

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
- [ ] Set explicit `CORS_ORIGINS` — never use `*` with auth enabled
- [ ] Set `DOCS_ENABLED=false` (default) to hide OpenAPI docs
- [ ] Configure `WEBHOOK_CLIENT_SECRET` to validate Graph webhook notifications
- [ ] Set `SIEM_ALLOWED_DOMAINS` if using SIEM forwarding
- [ ] Review `METRICS_ALLOWED_IPS` — defaults to private networks only
- [ ] Set `MCP_ALLOWED_HOSTS` if exposing the MCP SSE endpoint behind a reverse proxy

## Azure Key Vault (optional)

To store secrets in Azure Key Vault instead of `.env`:

1. Create a Key Vault and add these secrets:
   - `pulsar-client-secret` → `CLIENT_SECRET`
   - `pulsar-mongo-uri` → `MONGO_URI`
   - `pulsar-webhook-client-secret` → `WEBHOOK_CLIENT_SECRET`

2. Uncomment `azure-identity` and `azure-keyvault-secrets` in `backend/requirements.txt`

3. Set `AZURE_KEY_VAULT_NAME=your-keyvault-name` in `.env`

4. Ensure the container identity has `Get` permission on secrets (managed identity or service principal)

## Azure-native deployment

PULSAR runs natively on Azure Container Apps with Azure Cache for Redis and either a containerised MongoDB or Cosmos DB for MongoDB as the database. Secrets are managed via Key Vault and a managed identity — no credentials in environment variables.

For the full step-by-step guide, including the Cosmos DB migration path and custom domain setup, see **[DEPLOY-AZURE.md](DEPLOY-AZURE.md)**.

### Quick start (automated)

Run `deploy/bootstrap-tenant.ps1` first to create the Entra app registration, then provision the full Azure stack in one shot:

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
