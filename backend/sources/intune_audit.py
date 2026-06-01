from datetime import datetime, timedelta

from graph.auth import get_access_token
from utils.http import get_with_retry


def fetch_intune_audit(hours: int = 24, since: str | None = None, max_pages: int = 50) -> list[dict]:
    """
    Fetch Intune audit events via Microsoft Graph.
    Requires Intune audit permissions (e.g., DeviceManagementConfiguration.Read.All).
    """
    token = get_access_token()
    start_time = since or (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    url = f"https://graph.microsoft.com/v1.0/deviceManagement/auditEvents?$filter=activityDateTime ge {start_time}"
    headers = {"Authorization": f"Bearer {token}"}

    events = []
    pages = 0
    while url:
        if pages >= max_pages:
            raise RuntimeError(f"Aborting Intune pagination after {max_pages} pages.")
        try:
            res = get_with_retry(url, headers=headers, timeout=20)
            res.raise_for_status()
            body = res.json()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch Intune audit logs: {exc}") from exc

        events.extend(body.get("value", []))
        url = body.get("@odata.nextLink")
        pages += 1

    return [_normalize_intune(e) for e in events]


def _normalize_intune(e: dict) -> dict:
    """
    Map Intune audit event to normalized schema.
    """
    actor = e.get("actor", {}) or {}
    target = e.get("resources", [{}])[0] if e.get("resources") else {}

    initiated_by: dict = {"user": {}}
    if actor.get("auditActorType") == "Application" or actor.get("applicationDisplayName"):
        initiated_by["application"] = {
            "id": actor.get("applicationId"),
            "displayName": actor.get("applicationDisplayName"),
        }
    else:
        initiated_by["user"] = {
            "id": actor.get("userId"),
            "userPrincipalName": actor.get("userPrincipalName"),
            "displayName": actor.get("userName"),
            "ipAddress": actor.get("ipAddress"),
        }

    return {
        "id": e.get("id"),
        "activityDateTime": e.get("activityDateTime"),
        "category": e.get("category") or "Intune",
        "activityDisplayName": e.get("activity") or e.get("activityType"),
        "result": e.get("activityResult") or e.get("result"),
        "initiatedBy": initiated_by,
        "targetResources": [
            {
                "id": target.get("id"),
                "displayName": target.get("displayName")
                or target.get("modifiedProperties", [{}])[0].get("displayName"),
                "type": target.get("type"),
            }
        ]
        if target
        else [],
        "raw": e,
    }
