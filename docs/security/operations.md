# PULSAR security operations

## 1. Access matrix

Roles are Entra app roles on the API app. Sign-in requires assignment *and* one of `AUTH_ALLOWED_ROLES` (or a group in
`AUTH_ALLOWED_GROUPS`). `PulsarPrivacyReader` only widens what a signed-in user can see, so give it in addition to
`PulsarReader` or `PulsarAdmin`.

| Capability | Reader | Admin | + PrivacyReader | MCP API key |
|---|---|---|---|---|
| Search/view events, summaries, filter options, source health | yes | yes | yes | yes (search, get event, summary) |
| See privacy-gated services and operations | no | no | **yes** | no |
| Add tags/comments, bulk-tag (max 10 000 events) | yes | yes | yes | no |
| Save/delete own saved searches | yes | yes | yes | no |
| View alerts, change alert status | yes | yes | yes | no |
| View alert rules | yes | yes | yes | no |
| Create/edit/delete alert rules, trigger a manual fetch | no | **yes** | - | no |

Notes: with `AUTH_ADMIN_ROLES` empty every signed-in user is an admin (a startup warning says so). Privacy filtering
applies to alerts, summaries and filter options too. The MCP API key is one shared identity with no roles (KI-03).
Unauthenticated endpoints: `/health`, `/metrics` (IP allowlist), `/api/version`, `/api/config/auth`,
`/api/config/features`, `/api/webhooks/graph` (`clientState`), and `/.well-known/oauth-authorization-server`.

## 2. Data inventory and retention

| Data | Location | Contains | Retention |
|---|---|---|---|
| Audit events | MongoDB `events` | UPNs, display names, IPs, device/group/app names, operations, raw Microsoft payload | `RETENTION_DAYS` (default `0` = **kept forever**); set it to your policy |
| Tags and comments | in each event | free text written by users | with the event |
| Alerts | `alerts` | rule, actor, severity, status | no expiry; delete by policy |
| Alert rules | `alert_rules` | conditions, messages | until deleted |
| Saved searches | `saved_searches` | user's filter sets, keyed by Entra `sub` | until deleted by the user (max 50 each) |
| PULSAR audit trail | MongoDB database `micro_soc_audit` and container stdout | who did what, request ID, tags/comments text, MCP arguments | **no expiry** - define one; app user cannot delete |
| Rate-limit counters | Valkey | IP + path category | seconds to an hour |
| Secrets | `.env`, `.env.db`, `secrets/pulsar-ingestion.pem` | see section 3 | until rotated |

The events are personal data (identifiers of employees and administrators). The operator is the controller: set the
retention period, document the lawful basis, and delete on request using the event ID or UPN (Mongo shell). PULSAR
sends data out only to the optional alert webhook and SIEM webhook you configure.

## 3. Secrets and rotation

| Secret | Where | Rotate by |
|---|---|---|
| Ingestion certificate (valid 1 year) | `secrets/pulsar-ingestion.pem`; public part on the ingestion app | Follow *Rotating the ingestion certificate* in [DEPLOY.md](../../DEPLOY.md). Calendar a reminder 30 days before expiry |
| `MCP_API_KEY` | `.env` | New value (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), `docker compose up -d backend`, update clients |
| `WEBHOOK_CLIENT_SECRET` | `.env` and the Graph subscriptions' `clientState` | Set the new value and re-create the subscriptions with it; notifications are rejected in between |
| `MONGO_APP_PASSWORD` | `.env` | Edit `.env`, re-run the init script (it updates the existing user): `docker compose exec mongo sh -c 'mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" /docker-entrypoint-initdb.d/01-pulsar-user.js'`, then `docker compose up -d backend` |
| `REDIS_PASSWORD` | `.env` | Edit `.env`, `docker compose up -d redis backend` |
| MongoDB root password | `.env.db` | `db.changeUserPassword("root", "<new>")` in mongosh, then edit `.env.db` |
| Entra app credentials | Entra | The API app has none. After migrating from 1.x, run the bootstrap with `-RemoveLegacyCredentials` |

Files holding secrets must be mode `600`, owned by the deploying user, and excluded from backups that leave the host
unencrypted.

## 4. Backup and restore

```bash
# backup (events and alerts; the audit database is separate)
docker compose exec -T mongo sh -c 'mongodump -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --archive --gzip' > pulsar-$(date +%F).archive.gz
# restore into an empty instance
docker compose exec -T mongo sh -c 'mongorestore -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --archive --gzip --drop' < pulsar-YYYY-MM-DD.archive.gz
```

Keep copies off the host, encrypt them, and rehearse a restore at least once a year. Events can also be re-fetched from
Microsoft for the last 7 days (Office 365) or the Graph retention window, so the backup mainly protects tags, comments,
rules, alerts and the audit trail.

## 5. Monitoring

Container logs are JSON. Forward them to a system outside the host and alert on:

| Log event (substring) | Meaning |
|---|---|
| `Token verification failed`, `MCP OIDC auth failed`, `MCP auth failed: invalid API key` | Failed sign-ins (bad signature, expired, wrong tenant/issuer/scope, ID token) or key guessing |
| `Access denied: no allowed role or group`, `Access denied: admin role required` | Valid users probing beyond their role |
| HTTP 429 in the access log, `Rate limiter Redis error; failing closed` | Abuse, or Valkey down (the API then answers 429 to everyone) |
| `Graph webhook rejected: invalid clientState` | Spoofed notifications |
| `Failed to write audit trail` | Audit gap - treat as an incident |
| `SIEM forward failed`, `Notification failed after retries` | Alert delivery broken |
| `/api/source-health` status `error` | An ingestion source is failing (permission, certificate expiry) |

Review the `pulsar_audit` collection for `delete_rule`, `update_rule`, `bulk_tags` and `mcp_*` actions periodically.

## 6. Hardening checklist for a new deployment

- [ ] `AUTH_ENABLED=true`, `AUTH_ALLOWED_ROLES` and `AUTH_ADMIN_ROLES` set; `ALLOW_UNAUTHENTICATED` unset
- [ ] Reverse proxy terminates TLS, sets (not appends) `X-Forwarded-For`, and limits request bodies (1 MiB is enough)
- [ ] `TRUSTED_PROXIES` is the proxy's exact address, not the default private ranges (known issue KI-04)
- [ ] `METRICS_ALLOWED_IPS` is your Prometheus server only (KI-05)
- [ ] `CORS_ORIGINS` left empty unless a browser app on another origin needs the API
- [ ] `MCP_ALLOWED_HOSTS` set; `MCP_API_KEY` only if non-interactive clients need it
- [ ] `WEBHOOK_CLIENT_SECRET` set if Graph subscriptions are used
- [ ] `.env`, `.env.db` and the certificate are mode `600`; MongoDB and Valkey are not published to the host
- [ ] Egress from the host limited to Microsoft endpoints and the configured webhooks
- [ ] `RETENTION_DAYS` and an audit-trail retention policy decided; backups off-host and restore tested
- [ ] Image pinned by version or digest (not `latest`); updates tracked via Dependabot and the release notes
- [ ] Container logs (including the `audit` records) shipped off the host; alerts set up per section 5
- [ ] Azure deployments: a restricted MongoDB user created instead of `root`/admin (KI-13)
- [ ] Read [known-issues.md](known-issues.md) and decide which accepted risks are acceptable for this client
