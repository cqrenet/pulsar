from contextlib import suppress
from datetime import datetime, timedelta

import structlog
from config import settings
from graph.auth import get_access_token
from utils.common import utc_now
from utils.http import get_json, get_with_retry, post_with_retry, read_json_response
from utils.ssrf import validate_outbound_url

AUDIT_CONTENT_TYPES = {
    "Audit.Exchange": "Exchange admin audit",
    "Audit.SharePoint": "SharePoint admin audit",
    "Audit.General": "General (Teams/others)",
}


# Office 365 Management Activity API hard limits
_API_MAX_WINDOW_HOURS = 24
_API_MAX_LOOKBACK_DAYS = 7
logger = structlog.get_logger("pulsar.unified_audit")

_CONTENT_HOSTS = {
    "manage.office.com",
    "manage-gcc.office.com",
    "manage.office365.us",
    "manage.protection.apps.mil",
}


def _time_window(hours: int, since: str | None = None):
    end = utc_now().replace(tzinfo=None)  # the Management API wants naive UTC
    earliest_allowed = end - timedelta(days=_API_MAX_LOOKBACK_DAYS)
    max_window_start = end - timedelta(hours=_API_MAX_WINDOW_HOURS)

    if since:
        # Office 365 API expects format without Z
        start = datetime.fromisoformat(since.replace("Z", "+00:00")).replace(tzinfo=None)
        # Clamp: the API rejects windows > 24 h or start times > 7 days in the past.
        # If the watermark is stale (e.g. after a long outage), cap to the most recent
        # 24-hour window so the API accepts the request; subsequent fetches catch up.
        start = max(start, earliest_allowed, max_window_start)
    else:
        start = max(end - timedelta(hours=min(hours, _API_MAX_WINDOW_HOURS)), earliest_allowed)
    return start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_subscription(content_type: str, token: str, tenant_id: str):
    url = f"https://manage.office.com/api/v1.0/{tenant_id}/activity/feed/subscriptions/start"
    params = {"contentType": content_type}
    headers = {"Authorization": f"Bearer {token}"}
    with suppress(Exception):
        post_with_retry(url, params=params, headers=headers, timeout=10)


def _list_content(content_type: str, token: str, tenant_id: str, hours: int, since: str | None = None) -> list[dict]:
    start, end = _time_window(hours, since)
    url = f"https://manage.office.com/api/v1.0/{tenant_id}/activity/feed/subscriptions/content"
    params = {"contentType": content_type, "startTime": start, "endTime": end}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = get_with_retry(url, params=params, headers=headers, timeout=20, stream=True)
        if res.status_code in (400, 401, 403, 404):
            logger.error("Office 365 content listing failed", content_type=content_type, status=res.status_code)
            raise RuntimeError(f"{content_type} content listing failed (HTTP {res.status_code})")
        res.raise_for_status()
        return read_json_response(res, what=f"list {content_type} content") or []
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to list {content_type} content: {exc}") from exc


def _download_content(content_uri: str, token: str) -> list[dict]:
    try:
        validate_outbound_url(content_uri, https_only=True, allowed_domains=sorted(_CONTENT_HOSTS))
    except ValueError as exc:
        raise ValueError(f"Invalid audit content URI host: {exc}") from exc
    payload = get_json(
        content_uri,
        what="download audit content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if not isinstance(payload, list):
        raise RuntimeError("Audit content response was not a JSON list")
    return payload


def fetch_unified_audit(hours: int = 24, since: str | None = None, max_files: int = 50) -> list[dict]:
    """
    Fetch unified audit logs (Exchange, SharePoint, Teams policy changes via Audit.General)
    using the Office 365 Management Activity API.
    """
    # Need token for manage.office.com
    token = get_access_token("https://manage.office.com/.default")

    events = []

    for content_type in AUDIT_CONTENT_TYPES:
        _ensure_subscription(content_type, token, settings.TENANT_ID)
        contents = _list_content(content_type, token, settings.TENANT_ID, hours, since)
        for item in contents[:max_files]:
            content_uri = item.get("contentUri")
            if not content_uri:
                continue
            events.extend(_download_content(content_uri, token))

    return [_normalize_unified(e) for e in events]


def _normalize_unified(e: dict) -> dict:
    """
    Map unified audit log shape to the normalized schema used by the app.
    """
    actor_user = {
        "id": e.get("UserId"),
        "userPrincipalName": e.get("UserId"),
        "ipAddress": e.get("ClientIP"),
        "displayName": e.get("UserId"),
    }
    target = {
        "id": e.get("ObjectId") or e.get("OrganizationId"),
        "displayName": e.get("ObjectId"),
        "type": e.get("Workload"),
    }
    return {
        "id": e.get("Id") or e.get("RecordType"),
        "activityDateTime": e.get("CreationTime"),
        "category": e.get("Workload"),
        "activityDisplayName": e.get("Operation"),
        "result": e.get("ResultStatus"),
        "initiatedBy": {"user": actor_user},
        "targetResources": [target],
        "raw": e,
    }
