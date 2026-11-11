from datetime import timedelta

from graph.auth import get_access_token
from graph.resolve import resolve_directory_object, resolve_service_principal_owners
from utils.common import iso_z, utc_now
from utils.http import fetch_graph_pages


def fetch_audit_logs(hours: int = 24, since: str | None = None, max_pages: int = 50):
    """Fetch paginated directory audit logs from Microsoft Graph and enrich with resolved names."""
    token = get_access_token()
    start_time = since or iso_z(utc_now() - timedelta(hours=hours))
    url = f"https://graph.microsoft.com/v1.0/auditLogs/directoryAudits?$filter=activityDateTime ge {start_time}"
    events = fetch_graph_pages(
        url, headers={"Authorization": f"Bearer {token}"}, what="directory audit logs", max_pages=max_pages
    )
    return enrich_events(events, token)


def enrich_events(events, token):
    """
    Resolve actor/target IDs to readable names using Graph (requires Directory.Read.All).
    Adds _resolvedActor, _resolvedActorOwners, and per-target _resolved fields.
    """
    cache = {}
    owner_cache = {}

    for event in events:
        actor = event.get("initiatedBy", {}) or {}
        user = actor.get("user", {}) or {}
        sp = actor.get("servicePrincipal", {}) or {}
        app = actor.get("app", {}) or {}
        app_sp_id = app.get("servicePrincipalId") or app.get("servicePrincipalName")

        actor_id = user.get("id") or sp.get("id") or app_sp_id

        resolved_actor = resolve_directory_object(actor_id, token, cache) if actor_id else None
        actor_owners = []
        if resolved_actor and resolved_actor.get("type") == "servicePrincipal":
            actor_owners = resolve_service_principal_owners(resolved_actor.get("id"), token, owner_cache)

        event["_resolvedActor"] = resolved_actor
        event["_resolvedActorOwners"] = actor_owners

        for target in event.get("targetResources", []) or []:
            tid = target.get("id")
            if tid:
                resolved_target = resolve_directory_object(tid, token, cache)
                if resolved_target:
                    target["_resolved"] = resolved_target

    return events
