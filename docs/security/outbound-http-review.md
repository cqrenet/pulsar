# Outbound HTTP inventory

Every HTTP request the backend makes, with the controls on each. Current as of `main` after 3.1.0. The browser's own
requests (MSAL to `login.microsoftonline.com`, Alpine/MSAL from two SRI-pinned CDN paths) are covered by the CSP in
`backend/middleware.py`.

## Common rules

- **No redirects are followed** anywhere: every call passes `allow_redirects=False`, and a 3xx is a failure.
- **Response size:** JSON from Graph and the Office 365 Management API is streamed and capped at 10 MiB
  (`backend/utils/http.py`). The identity endpoints (token, OpenID configuration, JWKS) are read without a cap.
- **Retries:** `get_with_retry` / `post_with_retry` retry network errors 3 times with exponential backoff (2–10 s).
  Alert webhooks retry connection errors and timeouts 3 times. SIEM posts are not retried.
- **Errors:** failures are raised as `Failed to <action>: <exception>`. Upstream response bodies are not included.
- **Credentials:** bearer tokens are sent only to Microsoft hosts. Graph next links must stay on the first URL's HTTPS
  host, and Office 365 content URIs must be HTTPS on a Microsoft Management API host. Webhook and SIEM requests carry
  no credentials of ours.

## Inventory

| # | Call | Code | Destination comes from | Validation | Timeout | Body limit |
|---|---|---|---|---|---|---|
| 1 | Ingestion token (client credentials + signed assertion) | `graph/auth.py` | fixed: `login.microsoftonline.com/<TENANT_ID>` | fixed host | 15 s | none |
| 2 | OpenID configuration and JWKS (user token validation) | `auth.py` | fixed host; `jwks_uri` from the configuration | `jwks_uri` must be HTTPS on `login.microsoftonline.com` | 10 s | none |
| 3 | Directory audit pages | `graph/audit_logs.py` → `utils/http.fetch_graph_pages` | fixed `graph.microsoft.com`; next links from Graph | next link must keep the first URL's host; max 50 pages | 20 s | 10 MiB |
| 4 | Intune audit pages | `sources/intune_audit.py` → `fetch_graph_pages` | as #3 | as #3 | 20 s | 10 MiB |
| 5 | Directory object lookups | `graph/resolve.py` | fixed `graph.microsoft.com` + object ID from the event | 404 → not resolved | 10 s | 10 MiB |
| 6 | Office 365 subscription start | `sources/unified_audit.py` | fixed `manage.office.com` | errors ignored (idempotent) | 10 s | not read |
| 7 | Office 365 content listing | `sources/unified_audit.py` | fixed `manage.office.com` | 400–404 → error without body | 20 s | 10 MiB |
| 8 | Office 365 content download | `sources/unified_audit.py` | `contentUri` from #7 | HTTPS + host in the Microsoft Management API list; must be a JSON list | 30 s | 10 MiB |
| 9 | Alert webhook | `notifications.py` | `ALERT_WEBHOOK_URL` (operator config) | `utils/ssrf.py`: http/https, no internal IP literals or names; 2xx only | 15 s | not read |
| 10 | SIEM forwarding | `siem.py` | `SIEM_WEBHOOK_URL` (operator config) | HTTPS only, `utils/ssrf.py`, optional `SIEM_ALLOWED_DOMAINS`; 2xx only | 10 s | not read |

Microsoft endpoints are not rewritten for national clouds except the Office 365 content-host list, which includes the
GCC, GCC High and DoD hosts.

## What the URL checks do not cover

- **DNS:** `utils/ssrf.py` checks IP literals and known internal names, not what a hostname resolves to. Only #9 and #10
  take a configurable host, and both come from operator configuration, not from users.
  Restrict egress at the host or network ([known-issues.md KI-07](known-issues.md#ki-07-outbound-url-checks-do-not-resolve-dns---low-accepted)).
- **Identity endpoint responses (#1, #2)** are trusted to be reasonably sized; they come from fixed Microsoft hosts over
  TLS with redirects disabled.

## Tests

`backend/tests/test_refactor.py` and `backend/tests/test_security.py` cover: no redirects and 2xx-only for webhook and
SIEM, the 10 MiB limit and streaming, the content-URI host list, the Graph next-link host check, the JWKS host check,
SSRF rejections (schemes, internal IPs, metadata address, allowlist), and alert-card defanging.

## History

A focused review on 2026-11-12 found that alert webhook and SIEM requests followed redirects, so a validated URL could
redirect to an internal address, and that 3xx counted as delivered. It also raised these questions: unbounded upstream
responses, an unchecked `contentUri` origin, upstream error text in messages, and DNS rebinding. All except DNS
resolution were fixed in 3.0.0. This revision also stopped redirects on the identity calls and added the next-link and
JWKS host checks.
