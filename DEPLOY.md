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
   export PULSAR_VERSION=v1.0.0
   docker compose -f docker-compose.prod.yml pull
   ```

2. **Copy and edit environment variables**

   ```bash
   cp .env.example .env
   # Fill in TENANT_ID, CLIENT_ID, CLIENT_SECRET and any optional settings
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

PULSAR is Docker-based and runs anywhere containers run, including inside your Azure tenant. This section covers deploying PULSAR with Azure-managed infrastructure rather than self-hosted MongoDB and Redis.

### Azure Container Apps (recommended for Azure-native)

Azure Container Apps is the most natural fit — it runs containers without managing VMs, scales to zero when idle, and integrates with managed identity for secretless authentication.

A minimal setup:

1. **Provision supporting infrastructure**
   - Azure Container Apps environment
   - Azure Cache for Redis (Basic tier is sufficient for rate limiting)
   - Azure Cosmos DB for MongoDB (see below) or Azure Database for MongoDB (vCore)

2. **Create a managed identity** for the container app and assign it:
   - `Get` permission on your Key Vault secrets
   - Reader role on the resource group (if using managed identity for Key Vault)

3. **Deploy the container**

   ```bash
   az containerapp create \
     --name pulsar \
     --resource-group your-rg \
     --environment your-env \
     --image ghcr.io/cqrenet/pulsar:latest \
     --target-port 8000 \
     --ingress external \
     --env-vars AZURE_KEY_VAULT_NAME=your-kv TENANT_ID=... CLIENT_ID=... \
     --system-assigned
   ```

4. Set `MONGO_URI` and `REDIS_URL` to your Azure-managed connection strings via Key Vault (see Azure Key Vault section above).

> **Note:** Bicep/ARM templates and a fully scripted Azure deployment are planned for a future release. For now, manual provisioning or your own IaC is required.

### CosmosDB for MongoDB

CosmosDB's MongoDB-compatible API works with PULSAR without any code changes — only `MONGO_URI` needs updating. Admin audit log volumes are modest even for large tenants, so the default serverless or low-RU provisioned tier is sufficient for most deployments.

**Connection string:**

```
MONGO_URI=mongodb://your-account:your-key@your-account.mongo.cosmos.azure.com:10255/?ssl=true&retrywrites=false&maxIdleTimeMS=120000
```

The `retrywrites=false` parameter is required — CosmosDB does not support MongoDB retryable writes.

**Index creation:** PULSAR calls `setup_indexes()` at startup which creates the necessary indexes automatically. No manual index setup needed.

**Data migration from self-hosted MongoDB:**

```bash
# Export from existing instance
mongodump --uri="mongodb://root:password@localhost:27017" --db=micro_soc --out=./dump

# Restore to CosmosDB
mongorestore --uri="mongodb://your-account:your-key@your-account.mongo.cosmos.azure.com:10255/?ssl=true&retrywrites=false" --db=micro_soc ./dump/micro_soc
```

**Known limitations vs self-hosted MongoDB:**
- TTL index behaviour differs slightly — test with `RETENTION_DAYS` enabled before relying on it
- Heavy aggregation queries (e.g. `/api/summary` with large date ranges) consume more RUs than expected; monitor RU consumption during initial deployment

### Roadmap

The following Azure-native deployment improvements are planned:

- Bicep templates for full Azure deployment (Container Apps + CosmosDB + Key Vault + managed identity)
- Azure Container Instances deployment option for simpler single-container setups
- Managed identity authentication to Microsoft Graph (eliminating `CLIENT_SECRET` entirely)

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
