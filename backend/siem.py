import requests
import structlog
from config import SIEM_ALLOWED_DOMAINS, SIEM_ENABLED, SIEM_WEBHOOK_URL
from utils.ssrf import validate_outbound_url

logger = structlog.get_logger("pulsar.siem")


def forward_event(event: dict):
    """Forward a normalized event to the configured SIEM webhook."""
    if not SIEM_ENABLED or not SIEM_WEBHOOK_URL:
        return
    try:
        validate_outbound_url(SIEM_WEBHOOK_URL, https_only=True, allowed_domains=SIEM_ALLOWED_DOMAINS)
        res = requests.post(SIEM_WEBHOOK_URL, json=event, timeout=10)
        res.raise_for_status()
        logger.debug("Event forwarded to SIEM", event_id=event.get("id"))
    except Exception as exc:
        logger.warning("SIEM forward failed", error=str(exc))
