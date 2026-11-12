# PULSAR Release Notes

> **Current version:** see [`VERSION`](VERSION) for the latest SemVer.  
> **Full history:** [`CHANGELOG.md`](CHANGELOG.md)

---

## Latest Release Highlights

**3.x** makes the configuration secure by default (sign-in required, same-origin CORS, MongoDB root credentials kept
out of the backend container, hardened container) and moves to Python 3.14, `mcp` 2.x, redis-py 8 and Valkey 9. See
[CHANGELOG.md](CHANGELOG.md) for details and [DEPLOY.md](DEPLOY.md#upgrading-from-2x) to upgrade from 2.x.

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
- **Docker Compose deployment** (development and production configurations; an optional HTTPS-only nginx example)

### MCP Server (Claude Desktop / Cursor)

- **stdio transport** — local subprocess, no auth (development on a single-user machine only)
- **SSE transport** — remote/production endpoint at `/mcp/sse`
  - **API key auth** (`MCP_API_KEY`) — lightweight bearer-token gate
  - **Entra OIDC auth** (`AUTH_ENABLED`) — JWT validation with role/group ACLs
  - **OAuth 2.0 discovery** (`/.well-known/oauth-authorization-server`, RFC 8414) — enables Claude Desktop to auto-discover auth endpoints
  - **DNS rebinding protection** (`MCP_ALLOWED_HOSTS`) — declare public hostnames when running behind a reverse proxy
- **Available tools:** `search_events`, `get_event`, `get_summary`

### Authentication & Security

- Entra ID access tokens with app roles (reader, admin, privacy reader); PULSAR refuses to start without auth unless
  `ALLOW_UNAUTHENTICATED=true` is set for development
- Ingestion with a certificate credential; no client secret
- Privacy-sensitive services/operations hidden from users without `PRIVACY_SERVICE_ROLES`, on every read path
- One audit record per change, written to an insert-only database and to stdout
- Outbound HTTP: no redirects, bounded responses, Microsoft hosts pinned, SSRF checks on webhooks and SIEM
- Rate limiter fails closed when Valkey is unavailable
- Security review material: [docs/security/](docs/security/README.md), including the
  [known issues](docs/security/known-issues.md)

---

## Quick Start

```bash
cp .env.example .env
cp .env.db.example .env.db
# Paste the lines printed by .\deploy\bootstrap-tenant.ps1 into .env and set the passwords in both files
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

## Container Image

Pin a specific release in production rather than `latest`:

```bash
export PULSAR_VERSION=v3.1.1  # or see VERSION
docker pull ghcr.io/cqrenet/pulsar:${PULSAR_VERSION}
```

## Links

- [`README.md`](README.md) — Quick start, API reference, MCP setup
- [`DEPLOY.md`](DEPLOY.md) — Production deployment guide
- [`DEPLOY-AZURE.md`](DEPLOY-AZURE.md) — Azure Container Apps deployment
- [`CHANGELOG.md`](CHANGELOG.md) — Release history
- [Container Registry](https://github.com/cqrenet/pulsar/pkgs/container/pulsar)
