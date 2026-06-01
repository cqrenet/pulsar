from utils.http import get_with_retry


def _name_from_payload(payload: dict, kind: str) -> str:
    """Pick a readable name for a directory object payload."""
    if kind == "user":
        upn = payload.get("userPrincipalName") or payload.get("mail")
        display = payload.get("displayName")
        if display and upn and display != upn:
            return f"{display} ({upn})"
        return display or upn or payload.get("id") or "Unknown user"
    if kind == "servicePrincipal":
        return (
            payload.get("displayName")
            or payload.get("appDisplayName")
            or payload.get("appId")
            or payload.get("id")
            or "Unknown app"
        )
    if kind == "group":
        return payload.get("displayName") or payload.get("mail") or payload.get("id") or "Unknown group"
    if kind == "device":
        return payload.get("displayName") or payload.get("id") or "Unknown device"
    return payload.get("displayName") or payload.get("id") or "Unknown"


def _request_json(url: str, token: str) -> dict | None:
    try:
        res = get_with_retry(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return res.json()
    except Exception:
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
