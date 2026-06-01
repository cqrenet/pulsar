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

- Python 3.11+
- Docker Desktop (for the quickest start) or a local MongoDB instance
- An Entra app registration with **Application** permissions and admin consent:
  - `AuditLog.Read.All` — Entra directory audit logs
  - `ActivityFeed.Read` — Exchange/SharePoint/Teams via Office 365 Management Activity API
  - `DeviceManagementConfiguration.Read.All` — Intune audit events
- Optional: `AUTH_ENABLED=true` with `AUTH_TENANT_ID`/`AUTH_CLIENT_ID` to protect the API

## Quick Start

```bash
cp .env.example .env
# Edit .env: add TENANT_ID, CLIENT_ID, CLIENT_SECRET
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

PULSAR exposes an MCP interface in two forms:

**HTTP/SSE (production)** — mounted at `/mcp`, behind OIDC auth:

```
GET  /mcp/sse              — establish SSE stream
POST /mcp/messages/        — send tool calls
```

**stdio (local development)** — `python backend/mcp_server.py`:

Claude Desktop config:
```json
{
  "mcpServers": {
    "pulsar": {
      "command": "python",
      "args": ["/path/to/pulsar/backend/mcp_server.py"],
      "env": {"MONGO_URI": "mongodb://root:example@localhost:27017"}
    }
  }
}
```

Available tools:

| Tool | Description |
|------|-------------|
| `search_events` | Filter audit events by entity, service, operation, result, time range |
| `get_event` | Retrieve full event JSON by ID |
| `get_summary` | Aggregated activity summary for the last N days |

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
