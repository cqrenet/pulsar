<img src="docs/pulsar-logo.svg" alt="PULSAR — Platform for Unified Log Search, Alerting &amp; Review" width="480"/>

> Part of the [CQRE](https://cqre.net) M365 governance suite alongside [ASTRAL](https://github.com/cqrenet/astral).

PULSAR ingests Microsoft 365 admin audit events into MongoDB and exposes a UI, REST API, and MCP server for search, filtering, alerting, and SIEM forwarding. It answers the question: *"what happened in my tenant, when, and by whom?"*

## Components

- FastAPI backend with routes to fetch and search audit events.
- MongoDB for persistence (provisioned via Docker Compose).
- Microsoft Graph client for Entra directory and Intune audit events.
- Office 365 Management Activity API client for Exchange/SharePoint/Teams audit logs.
- Frontend served from the backend for filtering, searching, and reviewing events.
- Entra ID sign-in (access tokens) for the UI, API and MCP endpoint, with app roles. Required: PULSAR refuses to start without it unless `ALLOW_UNAUTHENTICATED=true` is set for local development.
- MCP server for Claude Desktop / Cursor integration (stdio and HTTP/SSE transports).
- Optional Azure Key Vault loader for secrets (needs a custom image build, see below).

## Prerequisites

- Docker Desktop (for the quickest start) or a local MongoDB instance
- PowerShell 7+ for the provisioning scripts (`winget install Microsoft.PowerShell` on Windows; `brew install --cask powershell` on macOS)
- An Entra app registration — created by the bootstrap script below

## Quick Start

**1. Create the ingestion certificate** (on the machine that will run PULSAR; the private key never leaves it)

```bash
mkdir -p secrets && cd secrets
openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=PULSAR Ingestion" -keyout key.pem -out pulsar-ingestion.crt
cat key.pem pulsar-ingestion.crt > pulsar-ingestion.pem && rm key.pem && cd ..
```

**2. Provision the Entra app registrations**

```powershell
.\deploy\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com" -PublicUrl "http://localhost:8000" `
    -PublicCertificatePath .\secrets\pulsar-ingestion.crt
```

The script creates two app registrations: **PULSAR Ingestion** (application permissions, authenticated by the certificate's public key) and **PULSAR** (the API that users and MCP clients sign in to, with app roles and assignment required). It grants admin consent, assigns you the admin roles, and prints the exact `.env` lines to copy. Upgrading from the old single-app setup? See [DEPLOY.md](DEPLOY.md#upgrading-from-14x).

**3. Configure and start**

```bash
cp .env.example .env
cp .env.db.example .env.db && chmod 600 .env .env.db
# .env: paste the lines printed by the bootstrap script and set MONGO_APP_PASSWORD and REDIS_PASSWORD.
# .env.db: set MONGO_INITDB_ROOT_PASSWORD. Generate each with: openssl rand -hex 32
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

## Configuration

Copy `.env.example` to `.env` and fill in your credentials. Key settings:

```bash
# Required: ingestion app (certificate in secrets/pulsar-ingestion.pem)
TENANT_ID=your-tenant-id
CLIENT_ID=your-ingestion-app-client-id

# Required (Docker Compose): openssl rand -hex 32. The MongoDB root password goes in .env.db instead
# (cp .env.db.example .env.db), so the backend container never receives it.
MONGO_APP_PASSWORD=...
REDIS_PASSWORD=...

# Protect the API with Entra OIDC (the API app)
AUTH_ENABLED=true
AUTH_TENANT_ID=your-tenant-id
AUTH_CLIENT_ID=your-api-client-id
AUTH_SCOPE=api://your-api-client-id/user_impersonation
AUTH_ALLOWED_ROLES=PulsarReader,PulsarAdmin
AUTH_ADMIN_ROLES=PulsarAdmin
PRIVACY_SERVICE_ROLES=PulsarPrivacyReader

# Optional: periodic background fetch
ENABLE_PERIODIC_FETCH=true
FETCH_INTERVAL_MINUTES=60

# Optional: data retention
RETENTION_DAYS=90
```

### Azure Key Vault

Instead of storing secrets in `.env`, store them in Azure Key Vault:

| Key Vault secret name        | Environment variable    |
|------------------------------|------------------------|
| `pulsar-mongo-uri`           | `MONGO_URI`             |
| `pulsar-webhook-client-secret` | `WEBHOOK_CLIENT_SECRET` |

The published image does not include the Azure SDK, so this needs your own build: add `azure-identity` and `azure-keyvault-secrets` to `backend/requirements.in`, regenerate the lock file (command at the top of that file), build the image, and set `AZURE_KEY_VAULT_NAME=your-keyvault-name`. On the published image, setting `AZURE_KEY_VAULT_NAME` stops startup with an error. Platform secret references (as the Azure Container Apps script uses) need none of this. The ingestion certificate is a file, not a Key Vault secret here: mount it and set `CLIENT_CERTIFICATE_PATH`.

## Security

- **Reviewing PULSAR?** Start with [docs/security/README.md](docs/security/README.md): scope, code map, a local
  sandbox, and the [known issues](docs/security/known-issues.md) we already know about.
- **Deploying it?** Work through the hardening checklist in
  [docs/security/operations.md](docs/security/operations.md#6-hardening-checklist-for-a-new-deployment). The most
  important items: set `TRUSTED_PROXIES` to your reverse proxy's address, assign app roles, and decide retention.
- **Found a vulnerability?** See [SECURITY.md](SECURITY.md).

## API Reference

*Auth* is what the caller needs: **none**, **user** (any signed-in user with an allowed role) or **admin**
(`AUTH_ADMIN_ROLES`). Privacy-gated services and operations are hidden from users without `PRIVACY_SERVICE_ROLES` on
every read.

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health` | none | Health check with MongoDB connectivity |
| `GET /metrics` | none (IP allowlist) | Prometheus metrics |
| `GET /api/version` | none | Running version |
| `GET /api/config/auth`, `GET /api/config/features` | none | Public sign-in parameters for the browser (tenant ID, client ID, scope) |
| `GET /.well-known/oauth-authorization-server` | none | OAuth discovery for MCP clients (when `MCP_CLIENT_ID` is set) |
| `POST /api/webhooks/graph` | `clientState` | Graph change notifications: validated and logged, nothing is ingested |
| `GET /api/events` | user | List events with filters: `services`, `actor`, `operation`, `result`, `start`, `end`, `search`, `correlation_id`, `include_tags`, `exclude_tags`, cursor paging |
| `GET /api/filter-options` | user | Distinct values for UI dropdowns |
| `GET /api/summary` | user | Aggregated activity: top services, operations, results, actors |
| `GET /api/source-health` | user | Last fetch status per source |
| `PATCH /api/events/{id}/tags` | user | Replace an event's tags |
| `POST /api/events/{id}/comments` | user | Add a comment |
| `POST /api/events/bulk-tags` | user | Tag every event matching the filters (max 10 000) |
| `GET/POST /api/saved-searches`, `DELETE /api/saved-searches/{id}` | user | The caller's own saved filters |
| `GET /api/alerts`, `GET /api/alerts/summary` | user | Alerts and counts |
| `PATCH /api/alerts/{id}/status` | user | Set alert status |
| `GET /api/rules` | user | List alert rules |
| `POST /api/rules`, `PUT /api/rules/{id}`, `DELETE /api/rules/{id}` | admin | Manage alert rules |
| `GET /api/fetch-audit-logs` | admin | Pull the latest audit events now (incremental, watermark-based) |
| `/mcp/sse`, `/mcp/messages/` | API key or user | MCP over SSE (see below) |

## MCP Server

PULSAR exposes an MCP server in two transport modes.

### Tools

| Tool | Description |
|------|-------------|
| `search_events` | Filter audit events by entity, service, operation, result, time range |
| `get_event` | Retrieve full event JSON by ID |
| `get_summary` | Aggregated activity summary for the last N days |

### stdio transport — local / Claude Desktop

Runs the MCP server as a subprocess. No auth is applied: whoever runs it with the database credentials reads everything that is not privacy-gated. Use it only for local development on a machine you alone use.

Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "pulsar": {
      "command": "python",
      "args": ["/path/to/pulsar/backend/mcp_server.py"],
      "env": {
        "MONGO_URI": "mongodb://pulsar:<MONGO_APP_PASSWORD>@localhost:27017/?authSource=admin"
      }
    }
  }
}
```

### SSE transport — remote / production

The MCP SSE endpoint is mounted at `/mcp/sse`. Auth is enforced by the `_McpAuthMiddleware` in `backend/routes/mcp.py`.

**Auth options (in priority order):**

| Method | When to use |
|--------|-------------|
| `MCP_API_KEY` | Claude Desktop (remote), AURORA service-to-service, any non-human caller |
| Entra ID JWT (`AUTH_ENABLED=true`) | Human users or systems that already have an Entra token |
| Neither set | Not possible unless `ALLOW_UNAUTHENTICATED=true` (local development only); then `/mcp` is open |

**Reverse proxy / DNS rebinding protection**

When PULSAR runs behind a reverse proxy, the MCP SDK's SSE transport rejects requests whose `Host` header does not match the allowed list. Set `MCP_ALLOWED_HOSTS` to your public hostname(s) so the MCP endpoint remains reachable:

```bash
MCP_ALLOWED_HOSTS=pulsar.yourtenant.example
```

Comma-separated values are supported. Loopback addresses (`localhost`, `127.0.0.1`, `[::1]`) are always permitted.

#### Option A — API key (recommended for Claude Desktop and AURORA)

Generate a key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set `MCP_API_KEY=<generated-key>` in your `.env` or container environment.

Claude Desktop config:
```json
{
  "mcpServers": {
    "pulsar": {
      "url": "https://pulsar.yourtenant.example/mcp/sse",
      "headers": {
        "Authorization": "Bearer <your-mcp-api-key>"
      }
    }
  }
}
```

The `x-api-key: <key>` header is also accepted as an alternative.

#### Option B — Entra ID (enterprise, human users)

Set `AUTH_ENABLED=true` and the Entra variables in `.env`. For Claude Desktop to discover the auth endpoints automatically, also set `MCP_CLIENT_ID` — this activates the `/.well-known/oauth-authorization-server` discovery endpoint (RFC 8414).

```json
{
  "mcpServers": {
    "pulsar": {
      "url": "https://pulsar.yourtenant.example/mcp/sse"
    }
  }
}
```

With the discovery endpoint active, Claude Desktop will initiate the OAuth flow automatically.

Both `MCP_API_KEY` and `AUTH_ENABLED` can be active simultaneously — service clients use the key, human users use Entra.

## Development

```bash
cd backend
python3.14 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt   # includes the production lock

# Lint and format
ruff check .
ruff format .

# Tests
pytest -q
```

## Maintenance

```bash
docker compose up -d mongo
docker compose run --rm backend python maintenance.py renormalize --limit 500
docker compose run --rm backend python maintenance.py dedupe
```

## Troubleshooting

- Ensure `TENANT_ID` and `CLIENT_ID` name the ingestion app, that it has the required permissions and admin consent, and that its registered certificate matches `secrets/pulsar-ingestion.pem`. Token errors show in the source-health dots and in `docker compose logs backend`.
- Management Activity API typically exposes ~7 days of history. Directory/Intune audit retention follows your tenant policy (commonly 30–90 days).
- The service uses the `micro_soc` database (`DB_NAME`) and keeps its own audit trail in `micro_soc_audit` (`AUDIT_DB_NAME`).
- If using Azure Key Vault, ensure the runtime identity has `Get` permission on secrets.
