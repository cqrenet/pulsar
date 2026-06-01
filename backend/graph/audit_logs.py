from datetime import datetime, timedelta

from graph.auth import get_access_token
from graph.resolve import resolve_directory_object, resolve_service_principal_owners
from utils.http import get_with_retry


def fetch_audit_logs(hours: int = 24, since: str | None = None, max_pages: int = 50):
    """Fetch paginated directory audit logs from Microsoft Graph and enrich with resolved names."""
    token = get_access_token()
    start_time = since or (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    next_url = f"https://graph.microsoft.com/v1.0/auditLogs/directoryAudits?$filter=activityDateTime ge {start_time}"
    headers = {"Authorization": f"Bearer {token}"}

    events = []
    pages_fetched = 0

    while next_url:
        if pages_fetched >= max_pages:
            raise RuntimeError(f"Aborting pagination after {max_pages} pages to avoid runaway fetch.")

        try:
            res = get_with_retry(next_url, headers=headers, timeout=20)
            res.raise_for_status()
            body = res.json()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch audit logs page: {exc}") from exc

        events.extend(body.get("value", []))
        next_url = body.get("@odata.nextLink")
        pages_fetched += 1

    return _enrich_events(events, token)


def _enrich_events(events, token):
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
