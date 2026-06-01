"""Rule-based alerting for PULSAR.

Rules are evaluated during event ingestion. Triggered alerts are stored in MongoDB
and optionally forwarded to a notification channel (webhook, Slack, Teams).

Deduplication: the same rule firing for the same actor within ALERT_DEDUPE_MINUTES
produces only one alert.
"""

from datetime import UTC, datetime, timedelta

import structlog
from config import ALERT_DEDUPE_MINUTES, ALERT_WEBHOOK_FORMAT, ALERT_WEBHOOK_URL
from database import db
from pymongo import ASCENDING

logger = structlog.get_logger("pulsar.rules")
rules_collection = db["alert_rules"]
alerts_collection = db["alerts"]


def load_rules() -> list[dict]:
    return list(rules_collection.find({"enabled": True}))


def evaluate_event(event: dict) -> list[dict]:
    """Evaluate a normalized event against stored alert rules."""
    triggered = []
    rules = load_rules()
    for rule in rules:
        if _matches(rule, event):
            if _is_duplicate(rule, event):
                logger.debug(
                    "Alert deduplicated",
                    rule=rule.get("name"),
                    event_id=event.get("id"),
                )
                continue
            triggered.append(rule)
            _create_alert(rule, event)
    return triggered


def _matches(rule: dict, event: dict) -> bool:
    conditions = rule.get("conditions", [])
    if not conditions:
        return False

    for cond in conditions:
        field = cond.get("field")
        op = cond.get("op", "eq")
        value = cond.get("value")
        event_value = _get_nested(event, field)

        if op == "eq" and event_value != value:
            return False
        if op == "neq" and event_value == value:
            return False
        if op == "contains" and (not isinstance(event_value, str) or value not in event_value):
            return False
        if op == "in" and event_value not in (value if isinstance(value, list) else [value]):
            return False
        if op == "after_hours":
            try:
                ts = datetime.fromisoformat(event.get("timestamp", "").replace("Z", "+00:00"))
                hour = ts.hour
                if 9 <= hour < 17:
                    return False
            except Exception:
                return False
        if op == "threshold_count":
            # Threshold rules are evaluated at query time, not per-event
            return False
    return True


def _get_nested(obj: dict, path: str):
    parts = path.split(".")
    val = obj
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def _is_duplicate(rule: dict, event: dict) -> bool:
    """Check if an alert for this rule + actor was recently created."""
    if ALERT_DEDUPE_MINUTES <= 0:
        return False
    cutoff = (datetime.now(UTC) - timedelta(minutes=ALERT_DEDUPE_MINUTES)).isoformat()
    actor = event.get("actor_display") or event.get("actor_upn") or "unknown"
    query = {
        "rule_id": str(rule.get("_id")),
        "actor": actor,
        "timestamp": {"$gte": cutoff},
    }
    return alerts_collection.count_documents(query, limit=1) > 0


def _create_alert(rule: dict, event: dict):
    actor = event.get("actor_display") or event.get("actor_upn") or "unknown"
    alert = {
        "timestamp": datetime.now(UTC).isoformat(),
        "rule_id": str(rule.get("_id")),
        "rule_name": rule.get("name", "Unnamed rule"),
        "severity": rule.get("severity", "medium"),
        "event_id": event.get("id"),
        "event_dedupe_key": event.get("dedupe_key"),
        "actor": actor,
        "message": rule.get("message", f"Rule '{rule.get('name')}' triggered"),
        "status": "open",  # open | acknowledged | resolved | false_positive
    }
    try:
        alerts_collection.insert_one(alert)
        logger.info("Alert created", rule=rule.get("name"), event_id=event.get("id"))
    except Exception as exc:
        logger.warning("Failed to create alert", error=str(exc))
        return

    # Send notification
    if ALERT_WEBHOOK_URL:
        try:
            from notifications import send_notification

            send_notification(
                webhook_url=ALERT_WEBHOOK_URL,
                format_type=ALERT_WEBHOOK_FORMAT,
                rule_name=rule.get("name", "Unnamed rule"),
                severity=rule.get("severity", "medium"),
                message=rule.get("message", ""),
                event=event,
            )
        except Exception as exc:
            logger.warning("Failed to send notification", error=str(exc))


def seed_default_rules():
    """Upsert pre-built PULSAR rule templates. Safe for concurrent startup."""
    # One-time cleanup: remove duplicates by name, keep the oldest (_id ascending)
    pipeline = [
        {"$sort": {"_id": ASCENDING}},
        {"$group": {"_id": "$name", "first_id": {"$first": "$_id"}}},
    ]
    seen = {doc["_id"]: doc["first_id"] for doc in rules_collection.aggregate(pipeline)}
    for name, keep_id in seen.items():
        rules_collection.delete_many({"name": name, "_id": {"$ne": keep_id}})

    defaults = [
        {
            "name": "Failed Conditional Access",
            "enabled": True,
            "severity": "high",
            "message": (
                "A Conditional Access policy evaluation failed. "
                "This may indicate a sign-in risk or policy misconfiguration."
            ),
            "conditions": [
                {"field": "service", "op": "eq", "value": "Directory"},
                {"field": "operation", "op": "contains", "value": "ConditionalAccess"},
                {"field": "result", "op": "neq", "value": "success"},
            ],
        },
        {
            "name": "After-Hours Admin Activity",
            "enabled": True,
            "severity": "medium",
            "message": "A privileged operation was performed outside business hours (9 AM – 5 PM).",
            "conditions": [
                {
                    "field": "service",
                    "op": "in",
                    "value": ["Directory", "UserManagement", "GroupManagement", "RoleManagement"],
                },
                {"field": "timestamp", "op": "after_hours"},
            ],
        },
        {
            "name": "New Application Registration",
            "enabled": True,
            "severity": "medium",
            "message": (
                "A new application was registered in Entra ID. Review for shadow IT or unauthorized integrations."
            ),
            "conditions": [
                {"field": "service", "op": "eq", "value": "ApplicationManagement"},
                {"field": "operation", "op": "contains", "value": "Add application"},
            ],
        },
        {
            "name": "Admin Role Assignment",
            "enabled": True,
            "severity": "high",
            "message": "A user was assigned an administrative role. Verify this was expected and authorized.",
            "conditions": [
                {"field": "service", "op": "eq", "value": "RoleManagement"},
                {"field": "operation", "op": "contains", "value": "Add member to role"},
            ],
        },
        {
            "name": "License Change",
            "enabled": True,
            "severity": "low",
            "message": "A license was assigned or removed from a user. Monitor for unexpected cost changes.",
            "conditions": [
                {"field": "service", "op": "eq", "value": "License"},
            ],
        },
        {
            "name": "Bulk User Deletion",
            "enabled": True,
            "severity": "high",
            "message": (
                "Multiple users were deleted in a short window. "
                "This may indicate a compromised admin account or cleanup activity."
            ),
            "conditions": [
                {"field": "service", "op": "in", "value": ["Directory", "UserManagement"]},
                {"field": "operation", "op": "contains", "value": "Delete user"},
            ],
        },
        {
            "name": "Device Compliance Failure",
            "enabled": True,
            "severity": "medium",
            "message": (
                "A device failed compliance evaluation. "
                "It may no longer meet your organization's security requirements."
            ),
            "conditions": [
                {"field": "service", "op": "eq", "value": "Intune"},
                {"field": "operation", "op": "contains", "value": "compliance"},
                {"field": "result", "op": "neq", "value": "success"},
            ],
        },
        {
            "name": "Exchange Transport Rule Change",
            "enabled": True,
            "severity": "high",
            "message": "An Exchange transport rule was modified. This could affect mail flow or security filtering.",
            "conditions": [
                {"field": "service", "op": "eq", "value": "Exchange"},
                {"field": "operation", "op": "contains", "value": "Transport rule"},
            ],
        },
        {
            "name": "Service Principal Credential Added",
            "enabled": True,
            "severity": "high",
            "message": "A new secret or certificate was added to a service principal. Verify this was expected.",
            "conditions": [
                {"field": "service", "op": "eq", "value": "ApplicationManagement"},
                {"field": "operation", "op": "contains", "value": "Add service principal credentials"},
            ],
        },
        {
            "name": "External Sharing Enabled",
            "enabled": True,
            "severity": "medium",
            "message": (
                "External sharing settings were modified on a SharePoint site or team. Review for data exposure risk."
            ),
            "conditions": [
                {"field": "service", "op": "in", "value": ["SharePoint", "Teams"]},
                {"field": "operation", "op": "contains", "value": "Sharing"},
            ],
        },
    ]

    inserted = 0
    for rule in defaults:
        try:
            result = rules_collection.replace_one(
                {"name": rule["name"]},
                rule,
                upsert=True,
            )
            if result.upserted_id:
                inserted += 1
        except Exception as exc:
            logger.warning("Failed to seed rule", rule=rule["name"], error=str(exc))
    if inserted:
        logger.info("Default PULSAR rules seeded", inserted=inserted, total=len(defaults))
