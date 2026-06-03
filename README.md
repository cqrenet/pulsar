<img src="docs/pulsar-logo.svg" alt="PULSAR — Platform for Unified Log Search, Alerting &amp; Review" width="480"/>

> Part of the [CQRE](https://cqre.net) M365 governance suite alongside [ASTRAL](https://github.com/cqrenet/astral).

PULSAR ingests Microsoft 365 admin audit events into MongoDB and exposes a UI, REST API, and MCP server for search, filtering, alerting, and SIEM forwarding. It answers the question: *"what happened in my tenant, when, and by whom?"*

## Components

- FastAPI backend with routes to fetch and search audit events.
- MongoDB for persistence (provisioned via Docker Compose).
- Microsoft Graph client for Entra directory and Intune audit events.
- Office 365 Management Activity API client for Exchange/SharePoint/Teams audit logs.
- Frontend served from the backend for filtering, searching, and reviewing events.
- Optional OIDC Bearer auth (Entra) to protect the API/UI and gate access by roles/groups.
- MCP server for Claude Desktop / Cursor integration (stdio and HTTP/SSE transports).
- Optional Azure Key Vault integration for secrets storage.

## Prerequisites

- Docker Desktop (for the quickest start) or a local MongoDB instance
- PowerShell 5.1+ for the provisioning script (pre-installed on Windows; `brew install --cask powershell` on macOS)
- An Entra app registration — created by the bootstrap script below

## Quick Start

**1. Provision the Entra app registration**

```powershell
.\deploy\bootstrap-tenant.ps1 -TenantName "contoso.onmicrosoft.com"
```

The script creates the app registration, assigns the required Graph and Office 365 Management API permissions, grants admin consent, and outputs the exact `.env` lines to copy.

**Existing deployment?** If PULSAR is already running with a manually created app registration, use `bootstrap-mcp-auth.ps1` instead — it adds MCP capabilities to your existing app without rotating the client secret or changing permissions.

**2. Configure and start**

```bash
cp .env.example .env
# Paste the lines printed by the bootstrap script
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

## Configuration

Copy `.env.example` to `.env` and fill in your credentials. Key settings:

```bash
# Required
TENANT_ID=your-tenant-id
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret

# Optional: protect the API with Entra OIDC
AUTH_ENABLED=true
AUTH_TENANT_ID=your-tenant-id
AUTH_CLIENT_ID=your-api-client-id
AUTH_ALLOWED_ROLES=Admins,SecurityOps

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
| `pulsar-client-secret`       | `CLIENT_SECRET`         |
| `pulsar-mongo-uri`           | `MONGO_URI`             |
| `pulsar-webhook-client-secret` | `WEBHOOK_CLIENT_SECRET` |

Set `AZURE_KEY_VAULT_NAME=your-keyvault-name` and uncomment the Azure identity packages in `backend/requirements.txt`.

## Security Hardening Checklist

Before deploying to production:

- [ ] Set `AUTH_ENABLED=true` and configure `AUTH_ALLOWED_ROLES` or `AUTH_ALLOWED_GROUPS`
- [ ] Set explicit `CORS_ORIGINS` (never use `*` with auth enabled)
- [ ] Set `DOCS_ENABLED=false` (default) to hide OpenAPI docs
- [ ] Configure `WEBHOOK_CLIENT_SECRET` to validate Graph webhook notifications
- [ ] Set `SIEM_ALLOWED_DOMAINS` if using SIEM forwarding
- [ ] Review `METRICS_ALLOWED_IPS` — defaults to private networks only
- [ ] Consider Azure Key Vault instead of `.env` for secrets

## API Reference

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check with MongoDB connectivity |
| `GET /metrics` | Prometheus metrics (IP-restricted) |
| `GET /api/version` | Running version |
| `GET /api/fetch-audit-logs` | Pull latest audit events (incremental, watermark-based) |
| `GET /api/events` | List events with filters: `service`, `actor`, `operation`, `result`, `start`, `end`, `search` |
| `GET /api/summary` | Aggregated activity summary: top services, operations, results, actors |
| `GET /api/filter-options` | Distinct values for UI dropdowns |
| `GET /api/source-health` | Last fetch status per source |
| `PATCH /api/events/{id}/tags` | Update event tags |
| `POST /api/events/{id}/comments` | Add a comment to an event |
| `POST /api/webhooks/graph` | Receive Microsoft Graph change notifications |
| `GET /api/rules` | List alert rules |
| `POST /api/rules` | Create an alert rule |
| `PUT /api/rules/{id}` | Update an alert rule |
| `DELETE /api/rules/{id}` | Delete an alert rule |

## MCP Server

PULSAR exposes an MCP server in two transport modes.

### Tools

| Tool | Description |
|------|-------------|
| `search_events` | Filter audit events by entity, service, operation, result, time range |
| `get_event` | Retrieve full event JSON by ID |
| `get_summary` | Aggregated activity summary for the last N days |

### stdio transport — local / Claude Desktop

Runs the MCP server as a subprocess. No auth is applied. Suitable for local development and single-user Claude Desktop setups where PULSAR runs on the same machine.

Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "pulsar": {
      "command": "python",
      "args": ["/path/to/pulsar/backend/mcp_server.py"],
      "env": {
        "MONGO_URI": "mongodb://root:example@localhost:27017"
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
| Neither set | Development / internal-only deployments with network-level protection |

**Reverse proxy / DNS rebinding protection**

When PULSAR runs behind a reverse proxy, FastMCP's SSE transport rejects requests whose `Host` header does not match the allowed list. Set `MCP_ALLOWED_HOSTS` to your public hostname(s) so the MCP endpoint remains reachable:

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
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Lint and format
ruff check ..
ruff format ..

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

- Ensure `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` match an app registration with the required permissions and admin consent.
- Management Activity API typically exposes ~7 days of history. Directory/Intune audit retention follows your tenant policy (commonly 30–90 days).
- The service uses the `micro_soc` database and `events` collection by default — adjust in `backend/config.py` if needed.
- If using Azure Key Vault, ensure the runtime identity has `Get` permission on secrets.
