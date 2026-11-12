# Known issues and limitations

This is the list of weaknesses, design limits and gaps that we know about, written for security reviewers. If you
find something that is not here, it is new to us: please report it (see [SECURITY.md](../../SECURITY.md)).

**Severity** is our own rating for a deployment that follows [DEPLOY.md](../../DEPLOY.md) and the hardening
checklist in [operations.md](operations.md). Rate it differently if your context differs. **Status** is one of:
*Accepted* (a deliberate trade-off), *Open* (we intend to change it), *Deployment* (the operator controls it).

Last reviewed: 2026-11-12, against `main` after release 3.1.0.

## Summary

| ID | Area | Issue | Severity | Status |
|---|---|---|---|---|
| KI-01 | Browser | Access token is held in `sessionStorage` | Medium | Accepted |
| KI-02 | Browser | CSP allows `style-src 'unsafe-inline'` | Low | Accepted |
| KI-03 | MCP | Shared API key: one identity, no expiry, printed by the bootstrap script | Medium | Accepted |
| KI-04 | Network | Default `TRUSTED_PROXIES` trusts every private address | Medium | Deployment |
| KI-05 | Network | Default `METRICS_ALLOWED_IPS` allows every private address | Low | Deployment |
| KI-06 | Availability | Rate limiting is per IP and fails closed when Valkey is down | Low | Accepted |
| KI-07 | SSRF | Outbound URL checks do not resolve DNS names | Low | Accepted |
| KI-08 | Integrity | Every signed-in reader can annotate events and change alert status | Low | Accepted |
| KI-09 | Integrity | The app's database user can modify and delete events | Medium | Accepted |
| KI-10 | Audit | Audit-trail writes fail open | Low | Accepted |
| KI-11 | Privacy | Audit trail and events have no expiry by default; the audit trail stores free text | Info | Deployment |
| KI-12 | Transport | No TLS between the backend and MongoDB/Valkey | Low | Accepted |
| KI-13 | Azure | The Azure path runs without the least-privilege database user | Medium | Open |
| KI-14 | Azure | In-app Key Vault loader needs libraries the published image lacks | Info | Open |
| KI-15 | Auth | Revoked access lasts until the token expires | Low | Accepted |
| KI-16 | Availability | Free-text search is an unanchored regex; the list query has no time limit | Low | Open |
| KI-17 | Availability | No request-body size limit in the app (except the Graph webhook) | Low | Deployment |
| KI-18 | MCP | SSE disconnects log an `AssertionError` | Info | Open |
| KI-19 | Privacy | Privacy gating works per service and operation only | Info | Accepted |
| KI-20 | Alerts | The generic alert webhook sends tenant-controlled strings unmodified | Low | Accepted |
| KI-21 | Disclosure | `/api/version` and `/api/config/auth` are unauthenticated | Info | Accepted |
| KI-22 | Supply chain | Images are not signed and ship no SBOM; builds are not reproducible | Low | Open |
| KI-23 | Process | Single maintainer; no second-person review; commits are not signed | Info | Accepted |
| KI-24 | Testing | Tests use mongomock and a fake Redis; production code has a mongomock fallback | Low | Open |
| KI-25 | Ingestion | The Graph webhook endpoint validates and logs notifications but processes nothing | Info | Accepted |
| KI-26 | Ingestion | Permissions are broader than strictly needed; the certificate key is a file | Info | Accepted |
| KI-27 | MCP | The stdio MCP server has no authentication | Low | Accepted |
| KI-28 | History | Releases before 3.0.0 have since-fixed weaknesses | Info | — |

## Details

### KI-01 Access token in `sessionStorage` - Medium, Accepted
MSAL keeps the access token in `sessionStorage` (`backend/frontend/js/auth.js`). Script running in the page could
read it. Mitigations: the CSP has no `unsafe-eval`, scripts come only from `'self'` and two exact, SRI-pinned CDN paths,
templates use Alpine's CSP build, and the UI has no `x-html`/`innerHTML`. A server-side session (BFF) would remove the
token from the browser; it is not planned yet.

### KI-02 `style-src 'unsafe-inline'` - Low, Accepted
Needed for inline styles in the UI. Scripts are not affected. CSS injection would still need an injection point.

### KI-03 Shared MCP API key - Medium, Accepted
`MCP_API_KEY` grants read access to everything that is not privacy-gated, as the single identity `mcp-api-key`. It has
no expiry and no scope; comparison is constant-time. `deploy/bootstrap-tenant.ps1` prints a suggested key in its output,
so the key can end up in terminal scrollback or logs. Prefer Entra sign-in for MCP clients; rotate the key (see
[operations.md](operations.md#3-secrets-and-rotation)); treat the script output as secret.

### KI-04 Default `TRUSTED_PROXIES` trusts every private address - Medium, Deployment
The default (`127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`) makes Docker setups work out of the box, but it
also means any client that reaches the backend *directly* from a private address can set `X-Forwarded-For`. That lets
it choose the IP used for rate limiting and the `/metrics` allowlist. The production compose file publishes the
backend on `127.0.0.1` only, which limits direct access to the host and containers on the same network. **Set
`TRUSTED_PROXIES` to the exact address of your reverse proxy.**

### KI-05 Default `METRICS_ALLOWED_IPS` allows every private address - Low, Deployment
`/metrics` exposes request counts per route and status, and fetch counters per source. No event data. Narrow it to
your Prometheus server.

### KI-06 Rate limiting per IP, fails closed - Low, Accepted
Limits are per client IP and path category (`backend/rate_limiter.py`). Users behind one NAT share a budget, and an
attacker with many IPs is not limited per account. When Valkey is unreachable, rate-limited endpoints return 429 for
everyone, so Valkey availability is API availability.

### KI-07 Outbound URL checks do not resolve DNS - Low, Accepted
`backend/utils/ssrf.py` rejects internal IP literals and known internal names, but a public name that resolves to a
private address passes. The affected URLs (`ALERT_WEBHOOK_URL`, `SIEM_WEBHOOK_URL`) come from operator configuration,
not from users; redirects are never followed. Restrict egress at the host or network.

### KI-08 Readers can annotate - Low, Accepted
Any signed-in user can add tags and comments, bulk-tag up to 10 000 matching events (in `replace` mode this removes
existing tags), and change alert status. Raw event data cannot be edited through the API. Annotations keep no version
history; the audit trail records who changed what. Restrict `AUTH_ALLOWED_ROLES` accordingly.

### KI-09 The app's database user can modify events - Medium, Accepted
The `pulsar` user has `readWrite` on the events database (`deploy/mongo-init/01-pulsar-user.js`), so a compromised
backend can alter or delete events, alerts and rules. Only the audit trail is insert-only. Microsoft keeps the source
data for its own retention window (Office 365: about 7 days via the API), so take off-host backups.

### KI-10 Audit-trail writes fail open - Low, Accepted
If MongoDB rejects the write, the action still completes and a warning is logged (`backend/audit_trail.py`). Every
record is also written to stdout first. Ship container logs off the host and alert on `Failed to write audit trail`.

### KI-11 Retention defaults - Info, Deployment
`RETENTION_DAYS` defaults to `0` (events are kept forever). The audit trail has no expiry and stores comment text and
MCP tool arguments, which can contain personal data. Decide both retention periods; see
[operations.md](operations.md#2-data-inventory-and-retention).

### KI-12 No TLS inside the Docker network - Low, Accepted
MongoDB and Valkey are on an `internal: true` network with authentication, but traffic is plaintext. Host isolation is
the control.

### KI-13 Azure path without the least-privilege database user - Medium, Open
[DEPLOY-AZURE.md](../../DEPLOY-AZURE.md) and `deploy/bootstrap-azure.ps1` connect the app to MongoDB as `root` (in
Container Apps) or as the Cosmos DB admin. The insert-only audit trail and the restricted `pulsar` user do not apply,
and the hardened container settings in `docker-compose.prod.yml` are not reproduced. The Azure path has been reviewed
but not tested end to end against 3.x. Create a restricted user yourself (the statements in `deploy/mongo-init/01-pulsar-user.js` work on
MongoDB; Cosmos DB needs its own role model).

### KI-14 Key Vault loader needs extra libraries - Info, Open
`backend/secrets_manager.py` imports `azure-identity` and `azure-keyvault-secrets`, which are not in the published
image. Setting `AZURE_KEY_VAULT_NAME` on that image stops startup with an error (it fails closed). Use the platform's
secret references instead (as the Azure script does), or build your own image with the libraries.

### KI-15 Revocation latency - Low, Accepted
Tokens are validated without a session store. Removing a user's role or disabling the account takes effect when the
token expires (Entra's default is 60–90 minutes). Signing keys are cached for an hour.

### KI-16 Search cost - Low, Open
Free-text search is a case-insensitive, unanchored regex over the stored raw event text. Counts and aggregations have a
5 s server-side limit (`backend/queries.py`); the event list query does not. An authenticated user can cause heavy
queries, bounded by the rate limit (default 120 per minute per IP).

### KI-17 No app-level body size limit - Low, Deployment
Only the Graph webhook enforces a limit (256 KiB). Other request bodies are validated by type and field length but not
by total size. Set a limit at the reverse proxy (the bundled `nginx/nginx.conf` uses 1 MiB).

### KI-18 MCP SSE disconnect `AssertionError` - Info, Open
When an MCP client disconnects from `/mcp/sse`, Starlette's `BaseHTTPMiddleware` logs
`AssertionError: Unexpected message` because the SSE app sends a final empty response. Clients are not affected and we
found no security impact; it is log noise. It predates the move to `mcp` 2.x.

### KI-19 Coarse privacy gating - Info, Accepted
`PRIVACY_SERVICES` and `PRIVACY_SENSITIVE_OPERATIONS` hide whole services or operation names. Events that are not
gated still contain names, UPNs and IP addresses, visible to every signed-in reader.

### KI-20 Generic alert webhook is not defanged - Low, Accepted
Slack and Teams cards neutralise links and markdown in tenant-controlled names. The `generic` JSON format sends the
values as they are; the receiving system must treat them as untrusted input.

### KI-21 Unauthenticated metadata endpoints - Info, Accepted
`/api/version` returns the exact version. `/api/config/auth` returns the tenant ID, API client ID and scope, which the
browser needs to sign in and which are not secrets. `/health` reports database connectivity.

### KI-22 Supply chain gaps - Low, Open
Dependencies are hash-locked, images and GitHub Actions are pinned by digest/SHA, and CI runs `pip-audit`, Trivy,
gitleaks and CodeQL. But release images are not signed (no cosign), no SBOM or provenance attestation is published,
and `apt-get upgrade` during the build makes images differ between builds. The UI loads Alpine and MSAL from CDNs
(SRI-pinned exact versions).

### KI-23 Process - Info, Accepted
PULSAR has one maintainer. `main` requires a pull request and six passing checks, but no second-person approval (it is
set to 0). Commits and tags are not signed. Release tags are immutable once pushed, and only admins can create them.

### KI-24 Test fidelity - Low, Open
Automated tests run against mongomock and an in-memory Redis stand-in, not real MongoDB or Valkey; the Docker Compose
stack is smoke-tested manually before releases. Production code in `backend/queries.py` and `backend/routes/events.py`
retries without `maxTimeMS` when a collection raises `TypeError`, to accommodate mongomock. With real PyMongo this never
happens, but it is test accommodation in production code.

### KI-25 Graph webhook is a stub - Info, Accepted
`POST /api/webhooks/graph` answers the validation handshake, checks `clientState` and logs notifications. It does not
trigger ingestion; ingestion is polling only (`ENABLE_PERIODIC_FETCH`).

### KI-26 Ingestion permissions and key storage - Info, Accepted
The ingestion app needs `AuditLog.Read.All`, `DeviceManagementApps.Read.All` and `ActivityFeed.Read`. These are read-only
but tenant-wide: `AuditLog.Read.All` also covers sign-in logs and `DeviceManagementApps.Read.All` also covers Intune
app configuration, because Microsoft offers nothing narrower for these endpoints. The private key is a file on the
host (mode 400, uid 10001), not in an HSM. The certificate is valid for one year and is not rotated automatically.

### KI-27 stdio MCP server is unauthenticated - Low, Accepted
`backend/mcp_server.py` (local development) applies no authentication. Whoever runs it with database credentials can
read everything that is not privacy-gated (it has no roles). Do not run it on shared machines.

### KI-28 Older releases - Info
Only the latest release is supported. Releases before 3.0.0 accepted tokens without `exp` and, up to 2.0.1, matched the
token issuer by substring. Before 2.0.0, ingestion used a client secret and the app connected to MongoDB as root.
[CHANGELOG.md](../../CHANGELOG.md) lists every security fix by release.
