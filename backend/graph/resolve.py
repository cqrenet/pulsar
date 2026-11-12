import structlog
from utils.http import get_with_retry, read_json_response

logger = structlog.get_logger("pulsar.resolve")

# Directory object kind -> (payload fields to try in order, label when none is present).
_NAME_RULES = {
    "servicePrincipal": (("displayName", "appDisplayName", "appId", "id"), "Unknown app"),
    "group": (("displayName", "mail", "id"), "Unknown group"),
    "device": (("displayName", "id"), "Unknown device"),
}


def _name_from_payload(payload: dict, kind: str) -> str:
    """Pick a readable name for a directory object payload."""
    if kind == "user":
        upn = payload.get("userPrincipalName") or payload.get("mail")
        display = payload.get("displayName")
        if display and upn and display != upn:
            return f"{display} ({upn})"
        return display or upn or payload.get("id") or "Unknown user"
    fields, fallback = _NAME_RULES.get(kind, (("displayName", "id"), "Unknown"))
    return next((payload[f] for f in fields if payload.get(f)), fallback)


def _request_json(url: str, token: str) -> dict | None:
    try:
        res = get_with_retry(url, headers={"Authorization": f"Bearer {token}"}, timeout=10, stream=True)
        if res.status_code == 404:
            res.close()
            return None
        payload = read_json_response(res, what="resolve Graph directory object")
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.debug("Graph lookup failed", url=url.split("?")[0], error=str(exc))
        return None


def resolve_directory_object(object_id: str, token: str, cache: dict[str, dict]) -> dict | None:
    """
    Resolve a directory object (user, servicePrincipal, group, device) to a readable name.
    Uses a simple multi-endpoint probe with caching to avoid extra Graph traffic.
    """
    if not object_id:
        return None
    if object_id in cache:
        return cache[object_id]

    probes = [
        ("user", f"https://graph.microsoft.com/v1.0/users/{object_id}?$select=id,displayName,userPrincipalName,mail"),
        (
            "servicePrincipal",
            f"https://graph.microsoft.com/v1.0/servicePrincipals/{object_id}?$select=id,displayName,appId,appDisplayName",
        ),
        ("group", f"https://graph.microsoft.com/v1.0/groups/{object_id}?$select=id,displayName,mail"),
        ("device", f"https://graph.microsoft.com/v1.0/devices/{object_id}?$select=id,displayName"),
    ]

    for kind, url in probes:
        payload = _request_json(url, token)
        if payload:
            resolved = {
                "id": payload.get("id", object_id),
                "type": kind,
                "name": _name_from_payload(payload, kind),
            }
            cache[object_id] = resolved
            return resolved

    cache[object_id] = None
    return None


def resolve_service_principal_owners(sp_id: str, token: str, cache: dict[str, list[str]]) -> list[str]:
    """Return a list of owner display names for a service principal."""
    if not sp_id:
        return []
    if sp_id in cache:
        return cache[sp_id]

    owners = []
    url = (
        f"https://graph.microsoft.com/v1.0/servicePrincipals/{sp_id}"
        "/owners?$select=id,displayName,userPrincipalName,mail"
    )
    payload = _request_json(url, token)
    for owner in (payload or {}).get("value", []):
        name = owner.get("displayName") or owner.get("userPrincipalName") or owner.get("mail") or owner.get("id")
        if name:
            owners.append(name)

    cache[sp_id] = owners
    return owners
