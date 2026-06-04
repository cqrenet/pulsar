# PULSAR Release Notes

> **Current version:** see [`VERSION`](VERSION) for the latest SemVer.  
> **Full history:** [`CHANGELOG.md`](CHANGELOG.md)

---

## Latest Release Highlights

PULSAR ingests Microsoft 365 admin audit events into MongoDB and exposes a web UI, REST API, and MCP server for search, filtering, alerting, and SIEM forwarding.

### Core Capabilities

- **Three audit sources**
  - Entra directory audit logs (via Microsoft Graph)
  - Intune audit logs (via Microsoft Graph)
  - Exchange, SharePoint, and Teams admin audits (via Office 365 Management Activity API)
- **Incremental fetch with watermarks** — only new events are pulled on each run
- **MongoDB persistence** with deduplication and optional TTL-based retention
- **Search and filter UI** with free-text search, service/actor/operation/result filters, and cursor-based pagination
- **Alert rules engine** — define conditions that trigger webhook notifications during ingestion
- **SIEM forwarding** — forward every ingested event to a webhook endpoint (Splunk, Sentinel, generic)
- **Prometheus metrics**, structured JSON logging, Redis-backed rate limiting
- **Docker Compose deployment** (dev and production configurations with nginx)

### MCP Server (Claude Desktop / Cursor)

- **stdio transport** — local subprocess, no auth (development / single-user)
- **SSE transport** — remote/production endpoint at `/mcp/sse`
  - **API key auth** (`MCP_API_KEY`) — lightweight bearer-token gate
  - **Entra OIDC auth** (`AUTH_ENABLED`) — JWT validation with role/group ACLs
  - **OAuth 2.0 discovery** (`/.well-known/oauth-authorization-server`, RFC 8414) — enables Claude Desktop to auto-discover auth endpoints
  - **DNS rebinding protection** (`MCP_ALLOWED_HOSTS`) — declare public hostnames when running behind a reverse proxy
- **Available tools:** `search_events`, `get_event`, `get_summary`

### Authentication & Security

- Optional OIDC Bearer token auth (Microsoft Entra) with role and group-based access control
- Optional Azure Key Vault integration for secrets storage
- Privacy-sensitive services/operations hidden from users without `PRIVACY_SERVICE_ROLES`
- SSRF protection on outbound webhooks and SIEM forwarding
- Rate limiter fails closed when Redis is unavailable

---

## Quick Start

```bash
cp .env.example .env
# Paste the lines printed by .\deploy\bootstrap-tenant.ps1
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

## Container Image

```bash
docker pull ghcr.io/cqrenet/pulsar:latest
```

Pin to a specific release:

```bash
export PULSAR_VERSION=v1.2.2  # or see VERSION
docker pull ghcr.io/cqrenet/pulsar:${PULSAR_VERSION}
```

## Links

- [`README.md`](README.md) — Quick start, API reference, MCP setup
- [`DEPLOY.md`](DEPLOY.md) — Production deployment guide
- [`DEPLOY-AZURE.md`](DEPLOY-AZURE.md) — Azure Container Apps deployment
- [`CHANGELOG.md`](CHANGELOG.md) — Release history
- [Container Registry](https://github.com/cqrenet/pulsar/pkgs/container/pulsar)
