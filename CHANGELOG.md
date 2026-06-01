# Changelog

All notable changes to PULSAR will be documented here.

## [1.0.3] — 2026-06-01

### Fixed

- Fixed JavaScript syntax error in `app.js` caused by a dangling `try` block left after AI feature cleanup, which prevented the frontend from loading.

## [1.0.2] — 2026-06-01

### Added

- Added PULSAR logo (`docs/pulsar-logo.svg`) with responsive dark/light colour schemes.
- Replaced topbar emoji icon with inline SVG logo in the web UI.
- Updated README header to display the logo image.

## [1.0.1] — 2026-06-01

### Fixed

- Removed orphaned AI feature remnants (Explain, Ask, LLM models, async jobs, and compiled bytecode) that were stripped for public release but left broken frontend code calling non-existent endpoints.
- Fixed misleading LLM comment in `.env.example` for `SIEM_ALLOWED_DOMAINS`.
- Rebranded all remaining AOC / "Admin Operations Center" references to PULSAR across frontend, backend, Dockerfile, nginx config, and documentation.

## [1.0.0] — 2025-06-01

Initial public release.

### Features

- Continuous ingestion of Microsoft 365 admin audit events from three sources:
  - Entra directory audit logs (via Microsoft Graph)
  - Intune audit logs (via Microsoft Graph)
  - Exchange, SharePoint, and Teams admin audits (via Office 365 Management Activity API)
- Incremental fetch with watermarks — only new events are pulled on each run
- MongoDB persistence with deduplication and optional TTL-based retention
- Search and filter UI with free-text search, service/actor/operation/result filters, and cursor-based pagination
- Alert rules engine — define conditions that trigger webhook notifications during ingestion
- SIEM forwarding — forward every ingested event to a webhook endpoint (Splunk, Sentinel, generic)
- MCP server (stdio and HTTP/SSE transports) with `search_events`, `get_event`, and `get_summary` tools for Claude Desktop / Cursor integration
- `/api/summary` endpoint for aggregated activity overview — used by AURORA
- Optional OIDC Bearer token auth (Microsoft Entra) with role and group-based access control
- Optional Azure Key Vault integration for secrets storage
- Prometheus metrics, structured JSON logging, Redis-backed rate limiting
- Docker Compose deployment (dev and production configurations with nginx)
