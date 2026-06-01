from contextlib import suppress
from datetime import datetime, timedelta

from graph.auth import get_access_token
from utils.http import get_with_retry, post_with_retry

AUDIT_CONTENT_TYPES = {
    "Audit.Exchange": "Exchange admin audit",
    "Audit.SharePoint": "SharePoint admin audit",
    "Audit.General": "General (Teams/others)",
}


# Office 365 Management Activity API hard limits
_API_MAX_WINDOW_HOURS = 24
_API_MAX_LOOKBACK_DAYS = 7


def _time_window(hours: int, since: str | None = None):
    end = datetime.utcnow()
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
        res = get_with_retry(url, params=params, headers=headers, timeout=20)
        if res.status_code in (400, 401, 403, 404):
            # Likely not enabled or insufficient perms; surface the text to the caller.
            raise RuntimeError(f"{content_type} content listing failed ({res.status_code}): {res.text}")
        res.raise_for_status()
        return res.json() or []
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to list {content_type} content: {exc}") from exc


def _download_content(content_uri: str, token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = get_with_retry(content_uri, headers=headers, timeout=30)
        res.raise_for_status()
        return res.json() or []
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to download audit content: {exc}") from exc


def fetch_unified_audit(hours: int = 24, since: str | None = None, max_files: int = 50) -> list[dict]:
    """
    Fetch unified audit logs (Exchange, SharePoint, Teams policy changes via Audit.General)
    using the Office 365 Management Activity API.
    """
    # Need token for manage.office.com
    token = get_access_token("https://manage.office.com/.default")
    from config import TENANT_ID  # local import to avoid cycles

    events = []

    for content_type in AUDIT_CONTENT_TYPES:
        _ensure_subscription(content_type, token, TENANT_ID)
        contents = _list_content(content_type, token, TENANT_ID, hours, since)
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
