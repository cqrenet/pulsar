import json
from datetime import UTC, datetime

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
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
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


def _render_summary(template: str, **fields) -> str:
    """Fill a mappings.yml summary template, with readable fallbacks for missing fields."""
    defaults = {"operation": "Event", "actor": "Unknown actor", "target": "target", "category": "Other"}
    values = {
        k: fields.get(k) or defaults.get(k, "")
        for k in ("operation", "actor", "target", "category", "result", "service")
    }
    try:
        return template.format(**values)
    except Exception:
        return ""


def make_dedupe_key(e: dict, target: str | None = None) -> str | None:
    """
    Build a stable key to prevent duplicates across sources.
    Preference order:
      - source event id (id) + category
      - fallback to timestamp + category + operation + first target label
    Accepts a raw source event or a stored document.
    """
    eid = e.get("id") or e.get("_id")
    category = e.get("category") or e.get("service")
    if eid:
        return "|".join(filter(None, [eid, category]))
    ts = e.get("activityDateTime") or e.get("timestamp")
    op = e.get("activityDisplayName") or e.get("operation")
    return "|".join(filter(None, [ts, category, op, target])) or None


def _display_actor(display_conf: dict, actor: dict, actor_label: str, actor_upn: str | None, first_target: str | None):
    """Choose the value and label the UI shows in its actor column, per the category's display mapping."""
    default_label = "Application" if (actor.get("application") or actor.get("app")) else "User"
    label = display_conf.get("actor_label", default_label)
    preference = display_conf.get("actor_field", "actor_display")
    if preference == "actor_upn" and actor_upn:
        return label, actor_upn
    if preference == "target_display" and first_target:
        return label, first_target
    return label, actor_label


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
    first_target = target_labels[0] if target_labels else None
    actor_label = _actor_display(actor, resolved_actor, actor_owners)
    actor_user = actor.get("user") or {}
    actor_upn = actor_user.get("userPrincipalName") or actor_user.get("mail")
    category = e.get("category")

    mapping = get_mapping()
    display_category = (mapping.get("category_labels") or {}).get(category, category or "Other")
    templates = mapping.get("summary_templates") or {}
    display_mapping = mapping.get("display") or {}
    summary = _render_summary(
        templates.get(category) or templates.get("default"),
        operation=e.get("activityDisplayName") or display_category,
        actor=actor_label,
        target=first_target,
        category=display_category,
        result=e.get("result"),
        service=e.get("loggedByService") or category,
    )
    actor_label_text, display_actor_value = _display_actor(
        display_mapping.get(category) or display_mapping.get("default", {}),
        actor,
        actor_label,
        actor_upn,
        first_target,
    )

    return {
        "id": e.get("id"),
        "correlation_id": correlation_id,
        "timestamp": _to_utc_iso(e.get("activityDateTime")),
        "service": category,
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
        "target_types": _target_types(targets),
        "display_category": display_category,
        "display_summary": summary,
        "raw": e,
        "raw_text": json.dumps(e, default=str),
        "dedupe_key": make_dedupe_key(e, first_target),
    }
