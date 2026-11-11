from datetime import timedelta

from graph.auth import get_access_token
from utils.common import iso_z, utc_now
from utils.http import fetch_graph_pages


def fetch_intune_audit(hours: int = 24, since: str | None = None, max_pages: int = 50) -> list[dict]:
    """
    Fetch Intune audit events via Microsoft Graph.
    Requires Intune audit permissions (DeviceManagementApps.Read.All is the application permission Graph requires for deviceManagement/auditEvents).
    """
    token = get_access_token()
    start_time = since or iso_z(utc_now() - timedelta(hours=hours))
    url = f"https://graph.microsoft.com/v1.0/deviceManagement/auditEvents?$filter=activityDateTime ge {start_time}"
    events = fetch_graph_pages(
        url, headers={"Authorization": f"Bearer {token}"}, what="Intune audit logs", max_pages=max_pages
    )
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
