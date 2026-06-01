import ipaddress

import requests
import structlog
from config import SIEM_ALLOWED_DOMAINS, SIEM_ENABLED, SIEM_WEBHOOK_URL

logger = structlog.get_logger("pulsar.siem")


def _validate_siem_url(url: str):
    """Prevent SSRF by rejecting internal/reserved addresses and enforcing domain allowlist."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError("SIEM_WEBHOOK_URL must use HTTPS")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise RuntimeError("SIEM_WEBHOOK_URL must have a valid hostname")
    blocked = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}
    if hostname in blocked:
        raise RuntimeError(f"SIEM_WEBHOOK_URL hostname '{hostname}' is not allowed")
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise RuntimeError(f"SIEM_WEBHOOK_URL IP '{hostname}' is not allowed")
    except ValueError:
        pass
    if SIEM_ALLOWED_DOMAINS:
        allowed = any(hostname == d or (d.startswith("*.") and hostname.endswith(d[1:])) for d in SIEM_ALLOWED_DOMAINS)
        if not allowed:
            raise RuntimeError(f"SIEM_WEBHOOK_URL domain '{hostname}' is not in SIEM_ALLOWED_DOMAINS")


def forward_event(event: dict):
    """Forward a normalized event to the configured SIEM webhook."""
    if not SIEM_ENABLED or not SIEM_WEBHOOK_URL:
        return
    try:
        _validate_siem_url(SIEM_WEBHOOK_URL)
        res = requests.post(SIEM_WEBHOOK_URL, json=event, timeout=10)
        res.raise_for_status()
        logger.debug("Event forwarded to SIEM", event_id=event.get("id"))
    except Exception as exc:
        logger.warning("SIEM forward failed", error=str(exc))
