from concurrent.futures import ThreadPoolExecutor

import requests
import structlog
from config import settings
from utils.ssrf import validate_outbound_url

logger = structlog.get_logger("pulsar.siem")

MAX_PARALLEL_POSTS = 4


def _post(session: requests.Session, event: dict) -> None:
    try:
        res = session.post(settings.SIEM_WEBHOOK_URL, json=event, timeout=10, allow_redirects=False)
        res.raise_for_status()
        if not 200 <= res.status_code < 300:
            raise requests.HTTPError(f"Unexpected SIEM response status {res.status_code}")
        logger.debug("Event forwarded to SIEM", event_id=event.get("id"))
    except Exception as exc:
        logger.warning("SIEM forward failed", error=str(exc))


def forward_events(events: list[dict]) -> None:
    """Forward normalized events to the configured SIEM webhook, a few at a time over one connection pool."""
    if not settings.SIEM_ENABLED or not settings.SIEM_WEBHOOK_URL or not events:
        return
    try:
        validate_outbound_url(settings.SIEM_WEBHOOK_URL, https_only=True, allowed_domains=settings.SIEM_ALLOWED_DOMAINS)
    except ValueError as exc:
        logger.warning("SIEM forward blocked", error=str(exc))
        return
    with requests.Session() as session, ThreadPoolExecutor(max_workers=MAX_PARALLEL_POSTS) as pool:
        list(pool.map(lambda e: _post(session, e), events))
