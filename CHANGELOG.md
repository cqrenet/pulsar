# Changelog

All notable changes to PULSAR will be documented here.

## [Unreleased]

### Changed

- **Phone layout.** At 640 px and below the header keeps only the essentials (brand, health dots, theme, Fetch, Refresh, Login), and the event table fits the screen with the operation text wrapping. The page no longer scrolls sideways on phones.
- **Sign-in splash.** When sign-in is required and the browser has no token, the UI shows a "Sign in with Microsoft" screen instead of an empty event list. It appears after sign-out and when a session expires.

## [3.1.2] — 2026-11-12

### Fixed

- **Sign-in failed with `Invalid issuer` for API apps that issue v1 access tokens.** Entra's default for an API app is
  v1 tokens, whose issuer is `https://sts.windows.net/<tenant>/`; 2.0.1 onwards accepted only the v2 issuer. Both
  issuers are now accepted, each bound exactly to `AUTH_TENANT_ID`. Rejections log the issuer found in the token.
  Installs whose API app already issues v2 tokens need no change.


## [3.1.1] — 2026-11-12

Security hardening and documentation for external review. No configuration changes are required.

### Security

- Microsoft identity calls (OpenID configuration, JWKS, token endpoint) no longer follow redirects; the JWKS URI must be
  HTTPS on `login.microsoftonline.com`.
- Graph pagination refuses `@odata.nextLink` URLs on another host, so the bearer token cannot be sent elsewhere.
- CSP adds `object-src 'none'`, `base-uri 'self'` and `frame-ancestors 'none'`.
- Token rejections for policy reasons (tenant, issuer, scope, ID token) and role denials are now logged.
- `nginx/nginx.conf` serves HTTPS only (HTTP redirects), limits bodies to 1 MiB, replaces client-supplied
  `X-Forwarded-For`, and drops the obsolete `X-XSS-Protection` header.
- `scripts/pulsar-live-baseline.py` requires `--base-url` instead of defaulting to a production host.

### Documentation

- New security reviewer guide ([docs/security/README.md](docs/security/README.md)) and a register of known issues with
  severities ([docs/security/known-issues.md](docs/security/known-issues.md)). The threat model, operations guide,
  outbound HTTP inventory, README, DEPLOY, DEPLOY-AZURE and AGENTS were corrected against the code.
- New `backend/seed_demo.py` fills a local sandbox with synthetic events, including hostile display names.

## [3.1.0] — 2026-11-12

No configuration changes are required. Operators who build their own images or run the app outside Docker need Python 3.14.

### Changed (dependency update)

- **Python 3.14** (was 3.11) in the image, CI and the hash-locked requirements. Run `pip-compile` with the same Python minor version; the lock header says how.
- **`mcp` 2.x** (was 1.30): `FastMCP` became `MCPServer`, and DNS-rebinding settings moved from the constructor to `sse_app()`. Verified with a real MCP client session over SSE (list tools, call tools, API key accepted, wrong key rejected).
- **`redis` 8.1** (was 5.3; `hiredis` is no longer needed) and **Valkey 9** (was 8) in the compose files. Rate-limit counters only, so no data migration is needed. `aclose()` replaces the deprecated `close()`.
- **GitHub Actions:** `actions/checkout` 7.0.1, `actions/setup-python` 7.1.0.
- **Image:** pip and setuptools are removed after the dependencies are installed. pip bundles its own urllib3 and msgpack, which scanners flag, and nothing needs pip at runtime.
- Known, unchanged by this update: the mounted MCP SSE endpoint logs an `AssertionError: Unexpected message` when a client disconnects (also with `mcp` 1.30). It is harmless to clients.

## [3.0.0] — 2026-11-12

**Major release: secure-by-default configuration.** If you run 2.x, follow [Upgrading from 2.x](DEPLOY.md#upgrading-from-2x) before updating. In short: set `AUTH_ENABLED=true` (or `ALLOW_UNAUTHENTICATED=true` for development), set `CORS_ORIGINS` explicitly, and move the MongoDB root credentials from `.env` to `.env.db` if you use the provided compose files.

### Security hardening (code audit preparation)

- **Breaking — fails closed without auth.** PULSAR refuses to start with `AUTH_ENABLED=false` unless `ALLOW_UNAUTHENTICATED=true` is set (local development only).
- **Breaking — `CORS_ORIGINS` defaults to empty** (same-origin only) instead of `*`.
- **Breaking (Docker Compose) — MongoDB root credentials moved to `.env.db`** (see `.env.db.example`) and are read only by the mongo container. Before, `env_file: .env` put the root password into the backend container, defeating the least-privilege `pulsar` user. Move `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` from `.env` to `.env.db` as `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD`.
- **Backend container hardening** in `docker-compose.prod.yml`: read-only root filesystem (`/tmp` is a tmpfs), all capabilities dropped, `no-new-privileges`, memory and pid limits.
- **Tokens must carry `exp`, `iss` and `aud`, and a matching `tid`.** Previously a token without `exp` verified.
- **Client-supplied `x-request-id`** is accepted only as 8–64 characters of `[A-Za-z0-9._-]`; anything else is replaced. It is written to audit records.
- **Privacy filter** also hides documents that have no `service` field (alerts created before the field existed).
- **Outbound HTTP:** alert webhooks, SIEM forwarding and all Graph/Office 365 calls no longer follow redirects, and webhook/SIEM deliveries count only 2xx as success. Upstream JSON responses are read with a 10 MiB limit, and Office 365 audit `contentUri` values must be HTTPS on a Microsoft Management API host. Failed Office 365 listings no longer put the upstream response text in errors.
- **Inbound webhook:** the Graph notification endpoint reads at most 256 KiB and 100 notifications per request and rejects malformed structures.
- **Database load:** summaries, filter options and counts run with a 5 s server-side time limit.
- **CI:** CodeQL (Python and JavaScript, `security-extended`, weekly and on every PR), a gitleaks scan of the full history, and a Trivy Dockerfile misconfiguration scan join lint, tests, `pip-audit` and the image scan.
- **Tests:** every `/api` route must require auth except a short allow-list, and the set of admin-only routes is pinned.

## [2.0.1] — 2026-11-11

### Fixed

- **`docker-compose.prod.yml` could not run the backend.** It was attached only to an `internal: true` network, so the published port `127.0.0.1:8000` was unreachable and ingestion could not reach Microsoft. The backend now also joins a normal `pulsar-edge` network; MongoDB and Valkey stay on the internal network. The image is unchanged from 2.0.0. If you copied the 2.0.0 compose file, add the `pulsar-edge` network to the backend.

- **Intune ingestion permission.** `deviceManagement/auditEvents` requires the application permission `DeviceManagementApps.Read.All`; the bootstrap script granted `DeviceManagementConfiguration.Read.All`, which returns 403. `deploy/bootstrap-tenant.ps1` now grants the right one. If you already ran the 2.0.0 script, add `DeviceManagementApps.Read.All` (application, admin consent) to the **PULSAR Ingestion** app.

## [2.0.0] — 2026-11-11

**Major release with breaking changes.** Existing installs must follow [Upgrading from 1.4.x](DEPLOY.md#upgrading-from-14x): new Entra app registration and ingestion certificate, `MONGO_APP_PASSWORD` / `REDIS_PASSWORD`, `AUTH_SCOPE`, and the one-time MongoDB user setup. Audit-trail `action` values are now semantic names (see below), so update any audit queries that filter on `post`/`patch`/`delete`.

### Changed (refactor)

- **Fix:** `/api/alerts/summary` now applies the same privacy filter as `/api/alerts`.
- **Fix:** `MCP_ALLOWED_HOSTS` is read through the settings (so `.env` works), not only from the process environment.
- **Fix:** Default alert rules are inserted only when missing — restarts no longer overwrite an admin's edits (e.g. a disabled rule). They now live in `default_rules.yml`. `after_hours` is documented as 09:00–17:00 UTC.
- Event filters are one `EventFilters` dependency shared by list and bulk-tag; the MCP search reuses it. Summary aggregation is shared by REST and MCP.
- `run_fetch` moved to `ingest.py` with a `SOURCES` registry; alert rules load once per run instead of once per event.
- One SSRF validator for alert webhooks and SIEM; `main.py` split into `middleware.py` and `routes/system.py`; startup uses a lifespan handler.
- Blocking MongoDB calls no longer run on the event loop (summary, saved searches, MCP tools).
- Frontend split into `js/` parts; the query-string builder, API helpers and download helper are no longer duplicated; bulk tagging asks for `append`/`replace` explicitly; load failures show a status message.
- SIEM forwarding posts events a few at a time over one connection pool; the Graph token cache is lock-protected.
- **Audit trail:** one record per mutating request, written by the middleware with the handler's action name and detail, the HTTP method, the status code (failed and denied attempts included) and the request ID. Handlers no longer write their own mutation records, so successful changes are no longer logged twice. **Breaking for audit queries:** `action` is now the semantic name (`update_tags`, `create_rule`, …) instead of `post`/`patch`/`delete` when the handler ran; saved-search changes are now audited. The correlation middleware now runs outermost, so rate-limit and error responses carry `x-request-id` too.
- Configuration is one `config.settings` object read at call time; the module-level copies of every setting are gone. List settings (roles, origins, domains, hosts) are parsed once by the settings class.
- Removed unused code: `arq` and `jobs.py`, `EventItem`, MCP schema constants, `threshold_count` rule operator, unused mapping helpers.

### Security

- **Privacy filtering on every read path** — `PRIVACY_SERVICES` / `PRIVACY_SENSITIVE_OPERATIONS` are now enforced in MCP tools, `/api/summary`, `/api/filter-options`, alerts, and tag/comment writes, not only `/api/events`. Operations-only privacy configs now work, and data is hidden from everyone when no `PRIVACY_SERVICE_ROLES` are set.
- **Admin role** — New `AUTH_ADMIN_ROLES` gates alert-rule changes and manual fetches.
- **Audit trail attribution** — API mutations were recorded as `anonymous` when auth was enabled; they now record the caller's `sub`. Rule changes, alert status changes, and MCP tool calls are logged explicitly.
- **X-Forwarded-For** — Only trusted proxies (`TRUSTED_PROXIES`) are believed, read right to left. Fixes a `/metrics` allowlist bypass and rate-limit bypass via a spoofed header. `/mcp` is now rate limited.
- **Access tokens only** — ID tokens are rejected as API credentials. **Breaking:** `AUTH_SCOPE` must be set when auth is enabled.
- **MCP inputs** — Free-text arguments are matched literally (no regex injection / ReDoS); `limit` and `days` are bounded.
- **Webhook** — Graph notifications are rejected unless `WEBHOOK_CLIENT_SECRET` is set; secrets and the MCP API key are compared in constant time.
- **Two app registrations, certificate auth** — Ingestion (app-only Graph/O365 permissions) and the user-facing API are separate Entra apps. Ingestion authenticates with a certificate whose private key is generated on the server; `CLIENT_SECRET` is gone. The API app gets app roles (`PulsarReader`, `PulsarAdmin`, `PulsarPrivacyReader`) and assignment required. `bootstrap-tenant.ps1` creates both and migrates an existing app; `bootstrap-mcp-auth.ps1` is removed. **Breaking:** see DEPLOY.md, "Upgrading from 1.4.x".
- **Fail-closed startup** — With `AUTH_ENABLED=true`, PULSAR refuses to start unless `AUTH_TENANT_ID`, `AUTH_CLIENT_ID` and `AUTH_SCOPE` are set. `AUTH_CLIENT_ID` no longer falls back to `CLIENT_ID`.
- **Least-privilege data stores** — The app connects to MongoDB as `pulsar` (readWrite on events, insert-only on the audit trail, which moves to its own database). Valkey requires a password, kept out of process arguments. Audit records are also written to stdout.
- **Strict CSP** — The UI uses Alpine's CSP build; the policy no longer allows `'unsafe-eval'`.
- **Alert cards** — Display names in Teams/Slack alerts are defanged (`hxxps[:]//`, broken markdown links) so tenant-controlled names cannot post clickable phishing links.
- **Supply chain** — Hash-locked Python dependencies (pip-compile), digest-pinned images, SHA-pinned GitHub Actions, Dependabot, `pip-audit` and a Trivy image scan in CI and before release. `mcp` is pinned below 2.0: an unpinned build would have pulled 2.x, which renamed `FastMCP`. The image applies Debian security updates and drops setuptools. The container runs as uid 10001.
- **Smaller fixes** — CSV export neutralises spreadsheet formulas; tags are limited to 100 characters; invalid IDs return 400/404 instead of 500; CSP allows exact CDN library paths only; HSTS header added; unused `python-jose` removed; `.dockerignore` added.

## [1.4.0] — 2026-06-08

### Added

- **Correlation ID tracking** — `normalize_event()` now extracts `correlationId` (Microsoft Graph) and `CorrelationId` (Office 365 Management API, from the raw payload) into a new `correlation_id` field. This is indexed in MongoDB and exposed in the API and frontend.
- **Correlation pivot view** — In the event detail expansion, a "Related by correlation →" button appears when an event has a `correlation_id`. Clicking it opens a timeline-style view of all events sharing that correlation ID, making it easy to trace multi-step operations across services.
- **Generic timeline view** — The existing actor timeline was refactored into a reusable timeline component that supports both actor-based (`search=`) and correlation-based (`correlation_id=`) filtering.

## [1.3.0] — 2026-06-08

### Added

- **Light / dark / auto theme toggle** — New theme selector in the header supports Light, Dark, and Auto (OS preference) modes. Theme preference is persisted in `localStorage`. The CSS uses `data-theme` attributes and `@media (prefers-color-scheme: light)` for the auto mode.
- **Automatic token refresh** — The frontend now intercepts 401 responses via `apiFetch()` and attempts silent MSAL token re-acquisition before showing a session-expired message. All API calls have been migrated from `fetch()` to `apiFetch()`.

### Fixed

- **Timezone display drift** — Added `_to_utc_iso()` normalisation in `normalize_event()` to ensure all timestamps carry an explicit `Z` UTC suffix. Office 365 Management API omits the suffix, which caused JavaScript's `Date()` to interpret the value as local time for non-UTC users.

## [1.2.3] — 2026-06-04

### Fixed

- **Event table horizontal overflow** — Changed `overflow-x: auto` to `overflow-x: clip` on `.table-wrap` to prevent unwanted horizontal scrollbars in the events table.

## [1.2.2] — 2026-06-03

### Fixed

- **Docker image: single gunicorn worker** — Reduced workers from 2 to 1. FastMCP's SSE transport stores sessions in-memory per process; with multiple workers, `POST /mcp/messages/` would round-robin across processes and hit "session not found" 404s. A single worker avoids this without requiring Redis-backed session storage. FastAPI is async/I-O-bound, so one worker handles all concurrency via the event loop.

## [1.2.1] — 2026-06-03

### Fixed

- **MCP SSE DNS rebinding protection** — FastMCP's transport security rejected requests arriving via a reverse proxy because the `Host` header did not match the default loopback allow-list. Added `MCP_ALLOWED_HOSTS` (comma-separated) so operators can declare their public hostname(s). Loopback addresses remain always permitted.
- **Bootstrap scripts now register Claude Desktop web redirect URI** — `bootstrap-tenant.ps1` and `bootstrap-mcp-auth.ps1` add `https://claude.ai/api/mcp/auth_callback` to the **Web** platform redirect URIs, enabling Claude Desktop's server-side OAuth callback to complete the auth code flow.

### Changed

- `README.md` and `DEPLOY.md` updated with `MCP_ALLOWED_HOSTS` guidance for reverse-proxy deployments.
- `.env.example` expanded with `MCP_ALLOWED_HOSTS` documentation.

## [1.2.0] — 2026-06-03

### Added

- **MCP SSE API key authentication** — `MCP_API_KEY` provides a lightweight alternative (or complement) to Entra OIDC for the MCP SSE endpoint. Clients send the key via `Authorization: Bearer <key>` or `x-api-key: <key>`.
- **OAuth 2.0 authorization server metadata** — Exposes `/.well-known/oauth-authorization-server` (RFC 8414) when `MCP_CLIENT_ID` and `AUTH_TENANT_ID` are set, enabling Claude Desktop and AURORA to discover Entra auth endpoints automatically.
- **Bootstrap PowerShell scripts** in `deploy/`:
  - `bootstrap-tenant.ps1` — creates the Entra app registration, assigns required Graph / Office 365 Management API permissions, grants admin consent, and prints ready-to-paste `.env` lines.
  - `bootstrap-mcp-auth.ps1` — adds MCP-specific scopes and redirect URIs to an existing app registration without rotating secrets.
- **Startup security warnings** — Logs a warning when the MCP endpoint is open (both `AUTH_ENABLED=false` and `MCP_API_KEY` unset).

### Changed

- **MCP SSE auth middleware rewritten as pure ASGI** — Replaces `BaseHTTPMiddleware` with a raw ASGI class to avoid response-body buffering that silently breaks SSE streaming.
- **Entra JWT audience validation** — Now accepts both the bare GUID and the `api://<guid>` Application ID URI form, matching how Entra v2.0 issues tokens for custom scopes.
- `README.md` and `DEPLOY.md` expanded with detailed MCP setup instructions (stdio vs SSE, API key vs Entra, OAuth discovery).
- `.env.example` reorganised with dedicated MCP configuration section.

## [1.1.1] — 2026-06-01

### Fixed

- Fixed event table DOM structure: `<template x-for>` inside `<tbody>` broke table layout (sticky header overlap) and prevented row expansion. Moved `<template>` to be a direct child of `<table>` with each event wrapped in its own `<tbody class="event-group">`.

## [1.1.0] — 2026-06-01

### Added

- **Entity timeline view**: click the ↗ button next to any actor name in the events table (or the "Actor timeline →" button in the expanded row) to open a chronological timeline of all events for that entity. Events are grouped by day (Today, Yesterday, etc.) with a visual spine, colour-coded service dots, and expandable detail rows. Supports pagination via "Load more events".

### Fixed

- Added missing hover-reveal styles for the actor timeline button (`.actor-cell`, `.actor-timeline-btn`).

## [1.0.6] — 2026-06-01

### Fixed

- Reverted multiple `<tbody>` approach; returned to single `<tbody>` with `<template>` iteration inside, using `:last-of-type` CSS selector for correct row border rendering across browsers.

## [1.0.5] — 2026-06-01

### Fixed

- Fixed invalid HTML table structure: `<template>` elements inside `<tbody>` caused inconsistent row rendering. Each event now uses its own `<tbody class="event-group">`, with CSS updated to match.

## [1.0.4] — 2026-06-01

### Changed

- Complete frontend redesign:
  - Tab-based navigation for Events, Alerts, and Rules.
  - Events displayed in a sortable table with expandable detail rows.
  - Inline tags, comments, and raw JSON view inside expanded rows.
  - Redesigned filter bar with advanced filters toggle, service multi-select dropdown, and export menu.
  - Relative timestamps (`5m ago`, `2h ago`) with full timestamp on hover.
  - Colour-coded service badges and result indicators.
  - Updated alert and rule list layouts with cleaner actions.
  - New modal design with backdrop click-to-close.
  - Sticky header, responsive breakpoints, and refined dark theme.

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
