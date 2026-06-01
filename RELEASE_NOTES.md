# PULSAR 1.0.0

**Platform for Unified Log Search, Alerting & Review**

Part of the [CQRE](https://cqre.net) M365 governance suite.

## What's New

PULSAR ingests Microsoft 365 admin audit events into MongoDB and exposes a UI, REST API, and MCP server for search, filtering, alerting, and SIEM forwarding.

### Features

- **Three audit sources**
  - Entra directory audit logs (via Microsoft Graph)
  - Intune audit logs (via Microsoft Graph)
  - Exchange, SharePoint, and Teams admin audits (via Office 365 Management Activity API)
- **Incremental fetch with watermarks** — only new events are pulled on each run
- **MongoDB persistence** with deduplication and optional TTL-based retention
- **Search and filter UI** with free-text search, service/actor/operation/result filters, and cursor-based pagination
- **Alert rules engine** — define conditions that trigger webhook notifications during ingestion
- **SIEM forwarding** — forward every ingested event to a webhook endpoint (Splunk, Sentinel, generic)
- **MCP server** (stdio and HTTP/SSE transports) with `search_events`, `get_event`, and `get_summary` tools for Claude Desktop / Cursor integration
- **Optional OIDC Bearer token auth** (Microsoft Entra) with role and group-based access control
- **Optional Azure Key Vault integration** for secrets storage
- **Prometheus metrics**, structured JSON logging, Redis-backed rate limiting
- **Docker Compose deployment** (dev and production configurations with nginx)

## Quick Start

```bash
cp .env.example .env
# Edit .env: add TENANT_ID, CLIENT_ID, CLIENT_SECRET
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

## Container Image

```bash
docker pull ghcr.io/cqrenet/pulsar:v1.0.0
```

## Links

- [README](https://github.com/cqrenet/pulsar/blob/main/README.md)
- [Deployment Guide](https://github.com/cqrenet/pulsar/blob/main/DEPLOY.md)
- [Container Registry](https://github.com/cqrenet/pulsar/pkgs/container/pulsar)
