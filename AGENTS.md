# PULSAR — Agent Development Guide

> **Platform for Unified Log Search, Alerting & Review**  
> Part of the CQRE M365 governance suite. Ingests Microsoft 365 admin audit events into MongoDB and exposes a web UI, REST API, and MCP server for search, filtering, alerting, and SIEM forwarding.

This guide is written for AI coding agents. It assumes no prior knowledge of the project.

---

## Project Overview

PULSAR answers the question: *"What happened in my tenant, when, and by whom?"*

It pulls audit data from three Microsoft sources:

1. **Entra directory audit logs** — via Microsoft Graph (`AuditLog.Read.All`)
2. **Intune audit logs** — via Microsoft Graph (`DeviceManagementApps.Read.All`)
3. **Exchange, SharePoint, Teams admin audits** — via Office 365 Management Activity API (`ActivityFeed.Read`)

Raw events are normalised into a common schema, stored in MongoDB, and made searchable through a FastAPI backend with a vanilla JS/Alpine.js frontend. Optional features include OIDC Bearer auth, rule-based alerting, SIEM forwarding, and an MCP (Model Context Protocol) server for Claude Desktop / Cursor integration.

**Current version:** see the [`VERSION`](VERSION) file.

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.14 |
| Web Framework | FastAPI |
| Server | Gunicorn with a single Uvicorn worker (MCP SSE sessions live in process memory) |
| Database | MongoDB 7 (default database `micro_soc`, collection `events`) |
| Cache / Rate Limiting | Valkey/Redis 8 |
| Frontend | Vanilla HTML/CSS/JS with Alpine.js 3.x (CSP build: no `unsafe-eval`, one expression per attribute) |
| Auth | Microsoft Entra OIDC Bearer tokens (optional) |
| Logging | `structlog` (structured JSON logging) |
| Metrics | `prometheus-client` |
| MCP | `mcp` library (stdio + HTTP/SSE transports) |
| HTTP Client | `requests` (outbound calls go through `utils/http.py`; `httpx` only in tests and the MCP SDK) |
| Testing | `pytest`, `mongomock`, `httpx` |
| Lint / Format | `ruff` (replaces flake8, black, isort) |
| Containerisation | Docker, Docker Compose |

---

## Directory Structure

```
pulsar/
├── .github/workflows/
│   ├── ci.yml                # Lint, format-check, pytest on push/PR to main
│   └── release.yml           # Build & push Docker image to GHCR on version tags
├── backend/
│   ├── main.py               # FastAPI app factory: routers, lifespan, startup checks
│   ├── middleware.py         # Correlation ID, metrics, security headers, rate limit, audit middleware
│   ├── ingest.py             # Fetch → normalise → upsert → alert → SIEM pipeline (+ SOURCES registry)
│   ├── queries.py            # EventFilters (query params → Mongo query), cursors, summaries
│   ├── default_rules.yml     # Alert rules seeded at startup (existing rules are never overwritten)
│   ├── config.py             # Pydantic-settings based configuration
│   ├── database.py           # MongoDB client, collections, index setup
│   ├── auth.py               # OIDC Bearer token validation
│   ├── metrics.py            # Prometheus metrics
│   ├── rate_limiter.py       # Redis-backed fixed-window rate limiting
│   ├── audit_trail.py        # Audit logging for API mutations
│   ├── rules.py              # Alert rules engine
│   ├── notifications.py      # Alert webhook dispatch
│   ├── siem.py               # SIEM forwarding logic
│   ├── secrets_manager.py    # Azure Key Vault integration
│   ├── watermark.py          # Incremental fetch watermark tracking
│   ├── maintenance.py        # CLI maintenance utilities (renormalize, dedupe)
│   ├── seed_demo.py          # Synthetic events for a local sandbox (security review, demos)
│   ├── mapping_loader.py     # YAML mapping loader
│   ├── mappings.yml          # Service/operation display mappings
│   ├── mcp_common.py         # MCP shared tool handlers
│   ├── mcp_server.py         # MCP stdio server entry point
│   ├── mcp_tools.py          # MCP tool definitions
│   ├── requirements.in       # Top-level production dependencies
│   ├── requirements.txt      # Hashed lock generated from requirements.in (pip-compile)
│   ├── requirements-dev.in   # Dev dependencies (pytest, mongomock, ruff, pip-audit)
│   ├── requirements-dev.txt  # Hashed lock: prod + dev
│   ├── Dockerfile            # Python 3.14-slim (digest-pinned), uid 10001, gunicorn
│   ├── frontend/             # Static UI files
│   │   ├── index.html
│   │   ├── app.js            # assembles the Alpine component from js/*
│   │   ├── js/               # api, auth, ui, events, alerts, timeline parts
│   │   └── style.css
│   ├── graph/                # Microsoft Graph API clients
│   │   ├── audit_logs.py
│   │   ├── auth.py
│   │   └── resolve.py
│   ├── models/               # Pydantic schemas
│   │   ├── api.py
│   │   └── event_model.py
│   ├── routes/               # FastAPI API routers
│   │   ├── alerts.py
│   │   ├── config.py
│   │   ├── events.py
│   │   ├── fetch.py
│   │   ├── health.py
│   │   ├── mcp.py
│   │   ├── system.py         # /health, /metrics, /api/version, OAuth discovery
│   │   ├── rules.py
│   │   ├── saved_searches.py
│   │   ├── summary.py
│   │   └── webhooks.py
│   ├── sources/              # Non-Graph audit log ingestion
│   │   ├── intune_audit.py
│   │   └── unified_audit.py
│   ├── tests/                # Test suite
│   │   ├── conftest.py
│   │   ├── test_api.py
│   │   ├── test_auth.py
│   │   ├── test_event_model.py
│   │   ├── test_rules.py
│   │   ├── test_security.py
│   │   └── test_frontend_csp.py
│   └── utils/                # Shared utilities
│       ├── common.py         # utc/iso helpers, literal-regex `contains`, `user_sub`
│       ├── http.py           # retrying GET/POST, `get_json`, Graph pagination
│       ├── net.py            # client_ip (trusted-proxy aware), network parsing
│       └── ssrf.py           # outbound URL validation (webhooks, SIEM)
├── nginx/
│   ├── nginx.conf            # Standalone reverse proxy config
│   └── ssl/                  # TLS certificate mount point
├── docker-compose.yml        # Dev: build backend from source
├── docker-compose.prod.yml   # Prod: pull from GHCR, healthchecks, internal DB network
├── .env.example              # Full configuration template
├── pyproject.toml            # ruff + pytest configuration
├── VERSION                   # SemVer string
├── README.md                 # Quick start, API reference, MCP setup
├── SECURITY.md               # Vulnerability reporting
├── docs/security/            # Reviewer guide, threat model, known issues, operations, outbound HTTP
├── scripts/                  # pulsar-live-baseline.py: read-only HTTP baseline of a deployment
├── DEPLOY.md                 # Production deployment guide
├── CHANGELOG.md              # Release history
└── RELEASE_NOTES.md          # Latest release highlights and quick reference
```

---

## Build and Test Commands

All backend work happens inside the `backend/` directory.

### Install dependencies

```bash
cd backend
pip install --require-hashes -r requirements-dev.txt   # prod + dev, hash-locked
# Changing dependencies: edit requirements*.in, then regenerate the locks (command at the top of requirements.in)
```

### Run the development server

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Or with Docker Compose (includes MongoDB + Valkey):

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up --build
```

### Linting and formatting

```bash
cd backend
ruff check .          # lint
ruff format .         # auto-format
ruff format --check . # check formatting without changing files
```

### Run tests

```bash
# From repo root (pytest reads pyproject.toml automatically)
pytest backend/tests -q

# Or from inside backend/
cd backend
pytest -q
```

Tests do **not** require a running MongoDB or Redis instance. Everything is mocked via `mongomock` and a hand-rolled `FakeRedis`.

---

## Code Style Guidelines

Configuration lives in `pyproject.toml`:

- **Target Python version:** 3.14
- **Line length:** 120 characters
- **Lint rules enabled:** E, F, I, N, W, UP, B, C4, SIM
  - `E` — pycodestyle errors
  - `F` — Pyflakes
  - `I` — isort (import sorting)
  - `N` — pep8-naming
  - `W` — pycodestyle warnings
  - `UP` — pyupgrade
  - `B` — flake8-bugbear
  - `C4` — flake8-comprehensions
  - `SIM` — flake8-simplify
- **Ignored rules:**
  - `E501` (line too long — handled by the formatter)
  - `B008` (function calls in argument defaults)
- **Docstring convention:** Google style (`tool.ruff.lint.pydocstyle.convention = "google"`)

**General conventions observed in the codebase:**

- Import order: stdlib → third-party → local modules (relative imports are common inside `backend/`).
- Module-level singletons for stateful resources (MongoDB client, Redis clients, JWKS cache).
- Use `structlog.get_logger("pulsar.<module>")` for logging.
- FastAPI dependencies are named `require_auth`, `user_can_access_privacy_services`, etc.
- Privacy-sensitive services/operations are gated by role checks at the query layer.
- Raw audit events from Microsoft APIs are always normalised through `normalize_event()` before storage.

---

## Testing Instructions

### Framework and configuration

- **Runner:** `pytest`
- **Config:** `pyproject.toml` sets `testpaths = ["backend/tests"]` and `pythonpath = ["backend"]`
- **Mocks:** `mongomock` for MongoDB; a custom `FakeRedis` for Redis; `fastapi.testclient.TestClient` for HTTP

### Fixtures (`backend/tests/conftest.py`)

The `client` fixture is heavily patched for test isolation:

- MongoDB collections replaced with `mongomock` collections
- `config.settings.AUTH_ENABLED` set to `False` (override settings with `monkeypatch.setattr("config.settings.NAME", value)`)
- Privacy settings emptied
- Redis replaced with in-memory `FakeRedis`
- MCP SSE transport security validation disabled for `TestClient` compatibility

### Test files

| File | Coverage |
|------|----------|
| `test_api.py` | End-to-end API tests: config, health, metrics, events CRUD, pagination, filters (including `correlation_id`), bulk tags, comments, saved searches, rules, alerts, webhooks, privacy filtering |
| `test_auth.py` | Unit tests for `_allowed()` and `require_auth()` with auth enabled/disabled, JWKS cache reset fixture |
| `test_event_model.py` | `normalize_event()` and `make_dedupe_key()` logic |
| `test_rules.py` | Rule condition matching (`eq`, `neq`, `contains`, `in`, `after_hours`) and `evaluate_event()` alert creation |
| `test_security.py` | Security regressions: token validation (issuer, tenant, scope, required claims, ID tokens), JWKS host check, privacy filtering on every read path (REST, MCP, alerts), admin role, audit records, X-Forwarded-For and request-ID handling, MCP input bounds and identity, alert-card defanging, certificate client assertion, startup refusal rules, and a behavioural check of every `/api` route as anonymous and as a reader |
| `test_refactor.py` | Shared helpers: SSRF checks, outbound redirects and response limits, content-URI and Graph next-link host checks, settings parsing, rate-limit categories, ingest pipeline |
| `test_frontend_csp.py` | Templates only use syntax Alpine's CSP build supports; the served CSP has no `unsafe-eval` |

### Adding new tests

- Place new test files in `backend/tests/` with the `test_*.py` naming convention.
- Use the `client` fixture for API-level tests.
- Use `monkeypatch` to toggle feature flags per-test.
- No external services are required; keep it that way.

---

## Security Considerations

The authoritative security documentation is [docs/security/](docs/security/README.md): threat model, known issues
(with severities), operations and the outbound HTTP inventory. Keep [known-issues.md](docs/security/known-issues.md)
current when a change adds, fixes or worsens a weakness.

PULSAR handles sensitive audit data and admin credentials. The following defences are implemented:

### Authentication and authorisation

- Entra ID access-token auth (`AUTH_ENABLED`). Startup refuses to run with it off unless `ALLOW_UNAUTHENTICATED=true` (development only).
- Tokens are verified against cached JWKS (RS256), with tenant/issuer/audience validation.
- Role-based and group-based access control via `AUTH_ALLOWED_ROLES` and `AUTH_ALLOWED_GROUPS`.
- Privacy-sensitive services/operations can be hidden from users without `PRIVACY_SERVICE_ROLES`.

### Network and transport

- CORS credentials are **automatically disabled** when `AUTH_ENABLED=true` with wildcard origins (`*`).
- The metrics endpoint (`/metrics`) is IP-restricted to private networks by default (`METRICS_ALLOWED_IPS`).
- OpenAPI docs are hidden in production (`DOCS_ENABLED=false`).
- Security headers middleware sets CSP, HSTS precursors, X-Frame-Options, etc.
- Generic exception handlers prevent information leakage.

### Webhooks and outbound requests

- Microsoft Graph webhook notifications require `clientState` validation (strongly recommended — set `WEBHOOK_CLIENT_SECRET`).
- Alert notifications and SIEM forwarding include SSRF protection (blocks private/loopback IPs).
- SIEM forwarding is HTTPS-only with a domain allowlist (`SIEM_ALLOWED_DOMAINS`).

### Secrets management

- Secrets can be loaded from Azure Key Vault before pydantic-settings reads them (`AZURE_KEY_VAULT_NAME`).
- Key Vault secret names: `pulsar-mongo-uri`, `pulsar-webhook-client-secret`.
- Two Entra apps: the **ingestion** app (`CLIENT_ID`) holds the app-only Graph/O365 permissions and authenticates with a certificate (`CLIENT_CERTIFICATE_PATH`, signed client assertion in `graph/auth.py`); the **API** app (`AUTH_CLIENT_ID`) has no credentials, defines the app roles, and requires assignment.

### Container security

- The Docker image runs as a non-root user (`pulsar:pulsar`, uid/gid 10001), with hash-locked dependencies and a digest-pinned base image. CI scans it with Trivy.
- The app connects to MongoDB as `pulsar` (`deploy/mongo-init`): readWrite on events, insert-only on the audit trail (`AUDIT_DB_NAME`). Valkey requires a password.
- Production Docker Compose puts MongoDB and Valkey on an `internal: true` network (`pulsar-internal`); only the backend is also on `pulsar-edge`, which gives it the published `127.0.0.1:8000` port and outbound access to Microsoft.
- MongoDB and Valkey are never exposed to the host in production.

---

## Deployment Process

### Development (local build)

```bash
cp .env.example .env
# Edit .env with TENANT_ID, CLIENT_ID, the Mongo/Redis passwords; put the ingestion cert in secrets/
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

### Production (pre-built image)

```bash
export PULSAR_VERSION=v3.1.0  # or see VERSION
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

- Uses image `ghcr.io/cqrenet/pulsar:${PULSAR_VERSION:-latest}`
- Healthchecks on all three services
- `pulsar-internal` (mongo, redis, backend) is marked `internal: true`; the backend also joins `pulsar-edge` for ingress and egress
- Backend binds to `127.0.0.1:8000` only — **always place a reverse proxy in front**

### Reverse proxy

PULSAR expects TLS termination and forwarding from a reverse proxy. Supported options documented in `DEPLOY.md`:

- Traefik (Docker label-based)
- Caddy
- Standalone nginx (config provided in `nginx/nginx.conf`)
- Azure Application Gateway / Container Apps

### CI/CD

- **CI** (`.github/workflows/ci.yml`): on push/PR to `main`, runs `ruff check`, `ruff format --check`, and `pytest -q` inside `./backend`.
- **Release** (`.github/workflows/release.yml`): on `v*` tags, builds and pushes `ghcr.io/cqrenet/pulsar:<tag>` and `:latest`.

---

## Key Architectural Patterns

1. **FastAPI router modularity** — Each domain has its own `APIRouter` mounted in `main.py`.
2. **Pydantic settings + `.env` driven config** — `config.py` uses `pydantic-settings` with `.env` support and optional Azure Key Vault pre-loading.
3. **Normalisation pipeline** — Raw events from three different Microsoft APIs are normalised into a single schema via `normalize_event()` before storage. Normalised documents include a `correlation_id` field when the source payload provides one (e.g., Microsoft Graph `correlationId`, Office 365 `CorrelationId`).
4. **Cursor-based pagination** — The events API uses base64-encoded `(timestamp|oid)` cursors, not offset paging.
5. **Event-driven alerting** — Alert rules are evaluated synchronously during ingestion (`run_fetch` → `evaluate_event`).
6. **MCP dual transport** — Shared tool handlers (`mcp_common.py`) are exposed via both stdio (`mcp_server.py`) and SSE (`routes/mcp.py`) transports.
7. **Rate limiter fails closed** — If Valkey/Redis is unavailable, rate-limited endpoints return `429` rather than allowing unbounded traffic.
8. **Module-level singletons** — MongoDB client, Redis clients, JWKS cache, and token cache are all module-level singletons.

---

## Environment Variables

All configuration is via environment variables or a `.env` file. See `.env.example` for the full template. Key required variables:

| Variable | Purpose |
|----------|---------|
| `TENANT_ID` | Microsoft Entra tenant ID |
| `CLIENT_ID` | App registration client ID |
| `CLIENT_CERTIFICATE_PATH` | PEM (key + cert) for the ingestion app; set by Docker Compose |
| `MONGO_APP_PASSWORD` / `REDIS_PASSWORD` | Passwords for the app's MongoDB user and Valkey (Docker Compose) |
| `MONGO_URI` | MongoDB connection string |
| `REDIS_URL` | Valkey/Redis connection string |

Notable optional variables:

| Variable | Purpose |
|----------|---------|
| `AUTH_ENABLED` | Enable OIDC Bearer token protection. PULSAR refuses to start with it off unless `ALLOW_UNAUTHENTICATED=true` (local development only) |
| `AUTH_TENANT_ID` / `AUTH_CLIENT_ID` / `AUTH_SCOPE` | OIDC provider config |
| `AUTH_ALLOWED_ROLES` / `AUTH_ALLOWED_GROUPS` | Comma-separated access control lists |
| `AUTH_ADMIN_ROLES` | Roles allowed to change alert rules and trigger fetches (empty = all signed-in users) |
| `TRUSTED_PROXIES` | IPs/CIDRs whose `X-Forwarded-For` is trusted for metrics allowlist and rate limiting |
| `ENABLE_PERIODIC_FETCH` / `FETCH_INTERVAL_MINUTES` | Background ingestion loop |
| `RETENTION_DAYS` | MongoDB TTL index for automatic data expiry (0 = disabled) |
| `CORS_ORIGINS` | Comma-separated allowed origins (default empty = same-origin only) |
| `DOCS_ENABLED` | Expose `/docs` and `/redoc` (default `false`) |
| `SIEM_ENABLED` / `SIEM_WEBHOOK_URL` / `SIEM_ALLOWED_DOMAINS` | Forward events to external SIEM |
| `ALERTS_ENABLED` / `ALERT_WEBHOOK_URL` / `ALERT_WEBHOOK_FORMAT` | Rule-based alerting |
| `WEBHOOK_CLIENT_SECRET` | Validate Microsoft Graph subscription notifications |
| `PRIVACY_SERVICES` / `PRIVACY_SENSITIVE_OPERATIONS` / `PRIVACY_SERVICE_ROLES` | Role-gated data hiding |
| `AZURE_KEY_VAULT_NAME` | Load secrets from Azure Key Vault instead of `.env` |
| `MCP_API_KEY` | API key for the MCP SSE endpoint (alternative/complement to OIDC) |
| `MCP_CLIENT_ID` | Entra app registration client ID for MCP OAuth discovery |
| `MCP_ALLOWED_HOSTS` | Comma-separated hostnames allowed by the MCP SSE transport (reverse proxy) |

---

## Maintenance Utilities

The `maintenance.py` module provides CLI commands for data housekeeping:

```bash
cd backend
python maintenance.py renormalize  # Re-run normalisation on stored events
python maintenance.py dedupe       # Remove duplicate events
```

These are intended to be run manually against the production database when event schemas change.

---

## Useful References

- `README.md` — Quick start, prerequisites, API reference, MCP setup
- `DEPLOY.md` — Production deployment, reverse proxy configs, Azure-native options, security hardening checklist
- `CHANGELOG.md` — Release history
- `RELEASE_NOTES.md` — Latest release highlights and quick reference
- `backend/mappings.yml` — Human-readable labels and summary templates for services/operations
