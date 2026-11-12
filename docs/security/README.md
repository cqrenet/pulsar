# Security review guide

Start here if you are assessing PULSAR. This page says what is in scope, where the security-relevant code is, how to
run it locally without a Microsoft tenant, and what we already know is weak.

## What PULSAR is

A single-tenant service that reads Microsoft 365 admin audit logs (Entra directory, Intune, Exchange/SharePoint/Teams)
with an app-only certificate credential, stores them in MongoDB, and serves them to signed-in users through a web UI, a
REST API and an MCP endpoint. Optional outputs: alert webhooks (Slack, Teams, generic) and SIEM forwarding.

## Scope

- **Code:** this repository at the latest release tag (or `main`). Only the latest release is supported.
- **Reference deployment:** `docker-compose.prod.yml` behind a TLS-terminating reverse proxy, as in
  [DEPLOY.md](../../DEPLOY.md). The Azure Container Apps path ([DEPLOY-AZURE.md](../../DEPLOY-AZURE.md)) is documented
  but has weaker database isolation and has not been tested end to end against 3.x (KI-13).
- **Not in scope:** Microsoft's services, the operator's reverse proxy and host, and any CQRE-operated instance.

## Documents

| Document | Contents |
|---|---|
| [threat-model.md](threat-model.md) | Data flow, trust boundaries, assets, STRIDE table with the code and tests behind each control |
| [known-issues.md](known-issues.md) | **Every weakness and limit we know about**, with our severity rating and status |
| [operations.md](operations.md) | Access matrix by role, data inventory and retention, secret rotation, backups, monitoring, hardening checklist |
| [outbound-http-review.md](outbound-http-review.md) | Every outbound HTTP call and its controls |
| [../../SECURITY.md](../../SECURITY.md) | How to report a vulnerability |
| [../../CHANGELOG.md](../../CHANGELOG.md) | Security fixes by release |

## Where the security-relevant code is

| Concern | Files (under `backend/` unless noted) |
|---|---|
| User authentication (Entra access tokens), roles, privacy filter | `auth.py` |
| MCP endpoint authentication (API key or Entra), DNS-rebinding settings | `routes/mcp.py`, `mcp_tools.py`, `mcp_common.py` |
| Security headers, CSP, rate limiting, request IDs, audit records | `middleware.py`, `rate_limiter.py`, `audit_trail.py` |
| Client IP and trusted proxies | `utils/net.py` |
| Settings, fail-closed startup | `config.py`, `main.py` |
| Query building (literal matching, cursors, time limits) | `queries.py`, `routes/events.py` |
| Outbound HTTP, SSRF checks, response limits | `utils/http.py`, `utils/ssrf.py`, `notifications.py`, `siem.py`, `sources/`, `graph/` |
| Ingestion credential (certificate client assertion) | `graph/auth.py` |
| Graph webhook receiver | `routes/webhooks.py` |
| Browser: token handling, API calls, output | `frontend/js/auth.js`, `frontend/js/api.js`, `frontend/index.html` |
| Container and data-store isolation | `Dockerfile`, `docker-compose.prod.yml`, `deploy/mongo-init/01-pulsar-user.js` |
| Entra app registrations | `deploy/bootstrap-tenant.ps1` |
| Tests that pin the controls | `tests/test_security.py`, `tests/test_refactor.py`, `tests/test_frontend_csp.py` |

## Build and test

Python 3.14, from the repository root:

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r backend/requirements-dev.txt
pytest backend/tests -q
cd backend && ruff check . && ruff format --check .
```

The tests use mongomock and an in-memory Redis stand-in, so no services are needed (and none are exercised: KI-24).

## Run a local sandbox without a Microsoft tenant

This runs the hardened production compose file with a locally built image, with authentication explicitly disabled
for the sandbox. Ingestion is off; synthetic data comes from `seed_demo.py`, which includes hostile-looking display
names (markdown links, spreadsheet formulas, HTML) for checking output encoding.

```bash
docker build ./backend -t ghcr.io/cqrenet/pulsar:local
mkdir -p secrets && openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj "/CN=sandbox" \
  -keyout /tmp/k.pem -out /tmp/c.pem && cat /tmp/k.pem /tmp/c.pem > secrets/pulsar-ingestion.pem && chmod 444 secrets/pulsar-ingestion.pem
cat > .env <<EOF
PULSAR_VERSION=local
TENANT_ID=sandbox
CLIENT_ID=sandbox
AUTH_ENABLED=false
ALLOW_UNAUTHENTICATED=true
MCP_API_KEY=$(openssl rand -hex 24)
MONGO_APP_PASSWORD=$(openssl rand -hex 32)
REDIS_PASSWORD=$(openssl rand -hex 32)
EOF
printf 'MONGO_INITDB_ROOT_USERNAME=root\nMONGO_INITDB_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 32)" > .env.db
chmod 600 .env .env.db
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec backend python seed_demo.py --count 200
```

The UI and API are then on `http://127.0.0.1:8000`, and MCP is at `/mcp/sse` with the `MCP_API_KEY` from `.env`.
`scripts/pulsar-live-baseline.py --base-url http://127.0.0.1:8000` records a read-only baseline of status codes and
headers.

**To test authentication and roles** you need an Entra tenant you control: create the two app registrations with
`deploy/bootstrap-tenant.ps1` (see [DEPLOY.md](../../DEPLOY.md)), set `AUTH_ENABLED=true` and the printed values, remove
`ALLOW_UNAUTHENTICATED`, and assign `PulsarReader`, `PulsarAdmin` and `PulsarPrivacyReader` to test users.

## Automated checks

Each push and pull request runs ruff, the test suite, `pip-audit` on the hash-locked dependencies, Trivy on the built
image and the Dockerfile, gitleaks over the full git history, and CodeQL (`security-extended`) for Python and
JavaScript. Results are in the repository's Actions and Security tabs. Dependabot watches pip, Docker, Docker Compose
and GitHub Actions dependencies.

## How the project is run

- One maintainer. Changes reach `main` through pull requests that must pass the six checks above; there is no
  second-person review (KI-23).
- Release tags (`v*`) are immutable once pushed and only admins can create them. Releases build and push
  `ghcr.io/cqrenet/pulsar:<tag>` after another Trivy scan.
- Secret scanning with push protection, private vulnerability reporting and Dependabot security updates are on.
