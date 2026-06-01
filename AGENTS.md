# PULSAR — Agent Development Guide

> **Platform for Unified Log Search, Alerting & Review**  
> Part of the CQRE M365 governance suite. Ingests Microsoft 365 admin audit events into MongoDB and exposes a web UI, REST API, and MCP server for search, filtering, alerting, and SIEM forwarding.

This guide is written for AI coding agents. It assumes no prior knowledge of the project.

---

## Project Overview

PULSAR answers the question: *"What happened in my tenant, when, and by whom?"*

It pulls audit data from three Microsoft sources:

1. **Entra directory audit logs** — via Microsoft Graph (`AuditLog.Read.All`)
2. **Intune audit logs** — via Microsoft Graph (`DeviceManagementConfiguration.Read.All`)
3. **Exchange, SharePoint, Teams admin audits** — via Office 365 Management Activity API (`ActivityFeed.Read`)

Raw events are normalised into a common schema, stored in MongoDB, and made searchable through a FastAPI backend with a vanilla JS/Alpine.js frontend. Optional features include OIDC Bearer auth, rule-based alerting, SIEM forwarding, and an MCP (Model Context Protocol) server for Claude Desktop / Cursor integration.

**Current version:** 1.0.0 (see `VERSION` file).

---

## Technology Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ |
| Web Framework | FastAPI |
| Server | Gunicorn + Uvicorn workers (production) |
| Database | MongoDB 7 (default database `micro_soc`, collection `events`) |
| Cache / Rate Limiting | Valkey/Redis 8 |
| Background Jobs | `arq` (Redis-based job queue — stubbed, not yet active) |
| Frontend | Vanilla HTML/CSS/JS with Alpine.js 3.x |
| Auth | Microsoft Entra OIDC Bearer tokens (optional) |
| Logging | `structlog` (structured JSON logging) |
| Metrics | `prometheus-client` |
| MCP | `mcp` library (stdio + HTTP/SSE transports) |
| HTTP Client | `httpx`, `requests` |
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
│   ├── main.py               # FastAPI app factory, middleware, startup/shutdown
│   ├── config.py             # Pydantic-settings based configuration
│   ├── database.py           # MongoDB client, collections, index setup
│   ├── auth.py               # OIDC Bearer token validation
│   ├── middleware.py         # Correlation ID middleware
│   ├── metrics.py            # Prometheus metrics
│   ├── rate_limiter.py       # Redis-backed fixed-window rate limiting
│   ├── audit_trail.py        # Audit logging for API mutations
│   ├── rules.py              # Alert rules engine
│   ├── notifications.py      # Alert webhook dispatch
│   ├── siem.py               # SIEM forwarding logic
│   ├── secrets_manager.py    # Azure Key Vault integration
│   ├── watermark.py          # Incremental fetch watermark tracking
│   ├── jobs.py               # arq worker configuration stub
│   ├── maintenance.py        # CLI maintenance utilities (renormalize, dedupe)
│   ├── mapping_loader.py     # YAML mapping loader
│   ├── mappings.yml          # Service/operation display mappings
│   ├── mcp_common.py         # MCP shared tool handlers
│   ├── mcp_server.py         # MCP stdio server entry point
│   ├── mcp_tools.py          # MCP tool definitions
│   ├── requirements.txt      # Production dependencies
│   ├── requirements-dev.txt  # Dev dependencies (pytest, mongomock, ruff)
│   ├── Dockerfile            # Python 3.11-slim, non-root user, gunicorn
│   ├── frontend/             # Static UI files
│   │   ├── index.html
│   │   ├── app.js
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
│   │   └── test_rules.py
│   └── utils/                # Shared utilities
│       └── http.py
├── nginx/
│   ├── nginx.conf            # Standalone reverse proxy config
│   └── ssl/                  # TLS certificate mount point
├── docker-compose.yml        # Dev: build backend from source
├── docker-compose.prod.yml   # Prod: pull from GHCR, healthchecks, internal network
├── .env.example              # Full configuration template
├── pyproject.toml            # ruff + pytest configuration
├── VERSION                   # SemVer string
├── README.md                 # Quick start, API reference, MCP setup
├── DEPLOY.md                 # Production deployment guide
├── CHANGELOG.md              # Release history
└── RELEASE_NOTES.md          # v1.0.0 feature summary
```

---

## Build and Test Commands

All backend work happens inside the `backend/` directory.

### Install dependencies

```bash
cd backend
pip install -r requirements.txt
pip install -r requirements-dev.txt
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

- **Target Python version:** 3.11
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
- `auth.AUTH_ENABLED` set to `False`
- Privacy settings emptied
- Redis replaced with in-memory `FakeRedis`
- MCP SSE transport security validation disabled for `TestClient` compatibility

### Test files

| File | Coverage |
|------|----------|
| `test_api.py` | End-to-end API tests: config, health, metrics, events CRUD, pagination, filters, bulk tags, comments, saved searches, rules, alerts, webhooks, privacy filtering |
| `test_auth.py` | Unit tests for `_allowed()` and `require_auth()` with auth enabled/disabled, JWKS cache reset fixture |
| `test_event_model.py` | `normalize_event()` and `_make_dedupe_key()` logic |
| `test_rules.py` | Rule condition matching (`eq`, `neq`, `contains`, `in`, `after_hours`) and `evaluate_event()` alert creation |

### Adding new tests

- Place new test files in `backend/tests/` with the `test_*.py` naming convention.
- Use the `client` fixture for API-level tests.
- Use `monkeypatch` to toggle feature flags per-test.
- No external services are required; keep it that way.

---

## Security Considerations

PULSAR handles sensitive audit data and admin credentials. The following defences are implemented:

### Authentication and authorisation

- Optional OIDC Bearer token auth via Microsoft Entra ID (`AUTH_ENABLED`).
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
- Key Vault secret names: `pulsar-client-secret`, `pulsar-mongo-uri`, `pulsar-webhook-client-secret`.

### Container security

- The Docker image runs as a non-root user (`pulsar:pulsar`).
- Production Docker Compose uses an `internal: true` network so only the backend is reachable from the host (via `127.0.0.1:8000`).
- MongoDB and Valkey are never exposed to the host in production.

---

## Deployment Process

### Development (local build)

```bash
cp .env.example .env
# Edit .env with TENANT_ID, CLIENT_ID, CLIENT_SECRET
docker compose up --build
```

- UI + API: http://localhost:8000
- Health: http://localhost:8000/health

### Production (pre-built image)

```bash
export PULSAR_VERSION=v1.0.0
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

- Uses image `ghcr.io/cqrenet/pulsar:${PULSAR_VERSION:-latest}`
- Healthchecks on all three services
- `pulsar-internal` network is marked `internal: true`
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
3. **Normalisation pipeline** — Raw events from three different Microsoft APIs are normalised into a single schema via `normalize_event()` before storage.
4. **Cursor-based pagination** — The events API uses base64-encoded `(timestamp|oid)` cursors, not offset paging.
5. **Event-driven alerting** — Alert rules are evaluated synchronously during ingestion (`run_fetch` → `evaluate_event`).
6. **MCP dual transport** — Shared tool handlers (`mcp_common.py`) are exposed via both stdio (`mcp_server.py`) and SSE (`routes/mcp.py`) transports.
7. **Rate limiter fails closed** — If Redis is unavailable, rate-limited endpoints return `503` rather than allowing unbounded traffic.
8. **Module-level singletons** — MongoDB client, Redis clients, JWKS cache, and token cache are all module-level singletons.

---

## Environment Variables

All configuration is via environment variables or a `.env` file. See `.env.example` for the full template. Key required variables:

| Variable | Purpose |
|----------|---------|
| `TENANT_ID` | Microsoft Entra tenant ID |
| `CLIENT_ID` | App registration client ID |
| `CLIENT_SECRET` | App registration client secret |
| `MONGO_URI` | MongoDB connection string |
| `REDIS_URL` | Valkey/Redis connection string |

Notable optional variables:

| Variable | Purpose |
|----------|---------|
| `AUTH_ENABLED` | Enable OIDC Bearer token protection |
| `AUTH_TENANT_ID` / `AUTH_CLIENT_ID` / `AUTH_SCOPE` | OIDC provider config |
| `AUTH_ALLOWED_ROLES` / `AUTH_ALLOWED_GROUPS` | Comma-separated access control lists |
| `ENABLE_PERIODIC_FETCH` / `FETCH_INTERVAL_MINUTES` | Background ingestion loop |
| `RETENTION_DAYS` | MongoDB TTL index for automatic data expiry (0 = disabled) |
| `CORS_ORIGINS` | Comma-separated allowed origins (default `*`) |
| `DOCS_ENABLED` | Expose `/docs` and `/redoc` (default `false`) |
| `SIEM_ENABLED` / `SIEM_WEBHOOK_URL` / `SIEM_ALLOWED_DOMAINS` | Forward events to external SIEM |
| `ALERTS_ENABLED` / `ALERT_WEBHOOK_URL` / `ALERT_WEBHOOK_FORMAT` | Rule-based alerting |
| `WEBHOOK_CLIENT_SECRET` | Validate Microsoft Graph subscription notifications |
| `PRIVACY_SERVICES` / `PRIVACY_SENSITIVE_OPERATIONS` / `PRIVACY_SERVICE_ROLES` | Role-gated data hiding |
| `AZURE_KEY_VAULT_NAME` | Load secrets from Azure Key Vault instead of `.env` |

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
- `RELEASE_NOTES.md` — v1.0.0 feature summary
- `backend/mappings.yml` — Human-readable labels and summary templates for services/operations
