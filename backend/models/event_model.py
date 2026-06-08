import json
from datetime import UTC
from datetime import datetime as _datetime

from mapping_loader import get_mapping


def _to_utc_iso(ts: str | None) -> str | None:
    """Normalise a timestamp string to UTC ISO-8601 with a Z suffix.

    Sources disagree: Graph APIs append Z, Office 365 Management API omits it
    (but the value is still UTC). A timezone-naive string fed to JavaScript's
    Date() is interpreted as local time, causing display drift for non-UTC users.
    """
    if not ts:
        return ts
    try:
        # fromisoformat handles Z (Python 3.11+), +HH:MM offsets, and naive strings.
        dt = _datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # If the source had no offset, the replace above won't have matched; the
        # string is still naive. Treat it as UTC.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        formatted = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        if formatted.endswith(".000Z"):
            formatted = formatted[:-5] + "Z"
        return formatted
    except ValueError:
        return ts


CATEGORY_LABELS = {
    "ApplicationManagement": "Application",
    "UserManagement": "User",
    "GroupManagement": "Group",
    "RoleManagement": "Role",
    "Device": "Device",
    "Policy": "Policy",
    "ResourceManagement": "Resource",
}


def _actor_display(actor: dict, resolved: dict = None, owners=None) -> str:
    """Choose a human-readable actor label."""
    if resolved and resolved.get("name"):
        name = resolved["name"]
        if resolved.get("type") == "servicePrincipal" and owners:
            owners_str = ", ".join(owners[:3])
            return f"{name} (owners: {owners_str})" if owners_str else name
        return name

    if not actor:
        return "Unknown actor"

    user = actor.get("user", {}) or {}
    sp = actor.get("servicePrincipal", {}) or {}
    app = actor.get("app", {}) or {}
    application = actor.get("application", {}) or {}
    upn = user.get("userPrincipalName") or user.get("mail")
    display = user.get("displayName")
    app_display = app.get("displayName") or application.get("displayName")
    app_id = app.get("id") or application.get("id")

    if display and upn and display != upn:
        return f"{display} ({upn})"

    return (
        display
        or upn
        or app_display
        or sp.get("displayName")
        or sp.get("appId")
        or app_id
        or actor.get("ipAddress")
        or user.get("id")
        or sp.get("id")
        or "Unknown actor"
    )


def _target_displays(targets: list) -> list:
    """Best-effort display labels for targets."""
    labels = []
    for t in targets or []:
        resolved = t.get("_resolved") or {}
        label = (
            resolved.get("name")
            or resolved.get("id")
            or t.get("displayName")
            or t.get("userPrincipalName")
            or t.get("logonId")
            or t.get("id")
            or ""
        )
        if label:
            labels.append(label)
    return labels


def _target_types(targets: list) -> list:
    """Collect target types for display mapping."""
    types = []
    for t in targets or []:
        resolved = t.get("_resolved") or {}
        t_type = resolved.get("type") or t.get("type")
        if t_type:
            types.append(t_type)
    return types


def _display_summary(operation: str, target_labels: list, actor_label: str, target_types: list, category: str) -> str:
    action = operation or category or "Event"
    target = target_labels[0] if target_labels else None
    t_type = target_types[0] if target_types else None

    target_piece = None
    if target and t_type:
        target_piece = f"{t_type.lower()}: {target}"
    elif target:
        target_piece = target

    pieces = [p for p in [action, target_piece] if p]
    if actor_label:
        pieces.append(f"by {actor_label}")
    return " | ".join(pieces)


def _render_summary(
    template: str, operation: str, actor: str, target: str, category: str, result: str, service: str
) -> str:
    try:
        return template.format(
            operation=operation or category or "Event",
            actor=actor or "Unknown actor",
            target=target or "target",
            category=category or "Other",
            result=result or "",
            service=service or "",
        )
    except Exception:
        return ""


def _make_dedupe_key(e: dict, normalized_fields: dict = None) -> str:
    """
    Build a stable key to prevent duplicates across sources.
    Preference order:
      - source event id (id) + category
      - fallback to timestamp + category + operation + first target label
    """
    norm = normalized_fields or {}
    eid = e.get("id") or e.get("_id") or norm.get("id")
    ts = e.get("activityDateTime") or e.get("timestamp") or norm.get("timestamp")
    category = e.get("category") or e.get("service") or norm.get("service")
    op = e.get("activityDisplayName") or e.get("operation") or norm.get("operation")
    target_labels = norm.get("target_displays") or []
    target = target_labels[0] if target_labels else None

    if eid:
        return "|".join(filter(None, [eid, category]))

    return "|".join(filter(None, [ts, category, op, target])) or None


def normalize_event(e):
    raw = e.get("raw") or {}
    correlation_id = (
        e.get("correlationId") or e.get("CorrelationId") or raw.get("correlationId") or raw.get("CorrelationId")
    )
    actor = e.get("initiatedBy", {})
    targets = e.get("targetResources", [])
    resolved_actor = e.get("_resolvedActor")
    actor_owners = e.get("_resolvedActorOwners", [])
    target_labels = _target_displays(targets)
    target_types = _target_types(targets)
    actor_label = _actor_display(actor, resolved_actor, actor_owners)
    actor_upn = (actor.get("user") or {}).get("userPrincipalName") or (actor.get("user") or {}).get("mail")
    first_target_label = target_labels[0] if target_labels else None
    category = e.get("category")
    mapping = get_mapping()
    category_labels = mapping.get("category_labels") or {}
    summary_templates = mapping.get("summary_templates") or {}
    display_mapping = mapping.get("display") or {}
    display_category = category_labels.get(category, category or "Other")

    operation = e.get("activityDisplayName")
    template = summary_templates.get(category) or summary_templates.get("default")
    summary = _render_summary(
        template,
        operation=operation,
        actor=actor_label,
        target=target_labels[0] if target_labels else None,
        category=display_category,
        result=e.get("result"),
        service=e.get("loggedByService") or e.get("category"),
    )

    display_conf = display_mapping.get(category) or display_mapping.get("default", {})
    actor_field_pref = display_conf.get("actor_field", "actor_display")
    default_actor_label = "Application" if (actor.get("application") or actor.get("app")) else "User"
    actor_label_text = display_conf.get("actor_label", default_actor_label)

    if actor_field_pref == "actor_upn" and actor_upn:
        display_actor_value = actor_upn
    elif actor_field_pref == "target_display" and first_target_label:
        display_actor_value = first_target_label
    else:
        display_actor_value = actor_label

    dedupe_key = _make_dedupe_key(
        e,
        {
            "id": e.get("id"),
            "timestamp": e.get("activityDateTime"),
            "service": e.get("category"),
            "operation": e.get("activityDisplayName"),
            "target_displays": target_labels,
        },
    )

    return {
        "id": e.get("id"),
        "correlation_id": correlation_id,
        "timestamp": _to_utc_iso(e.get("activityDateTime")),
        "service": e.get("category"),
        "operation": e.get("activityDisplayName"),
        "result": e.get("result"),
        "actor": actor,
        "actor_resolved": resolved_actor,
        "actor_owner_names": actor_owners,
        "actor_display": actor_label,
        "actor_upn": actor_upn,
        "display_actor_label": actor_label_text,
        "display_actor_value": display_actor_value,
        "targets": targets,
        "target_displays": target_labels,
        "target_types": target_types,
        "display_category": display_category,
        "display_summary": summary,
        "raw": e,
        "raw_text": json.dumps(e, default=str),
        "dedupe_key": dedupe_key,
    }
