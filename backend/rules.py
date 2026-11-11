"""Rule-based alerting for PULSAR.

Rules are evaluated during event ingestion. Triggered alerts are stored in MongoDB
and optionally forwarded to a notification channel (webhook, Slack, Teams).

Deduplication: the same rule firing for the same actor within ALERT_DEDUPE_MINUTES
produces only one alert.
"""

from datetime import datetime, timedelta
from pathlib import Path

import structlog
import yaml
from config import ALERT_DEDUPE_MINUTES, ALERT_WEBHOOK_FORMAT, ALERT_WEBHOOK_URL
from database import alerts_collection, rules_collection
from notifications import send_notification
from utils.common import utc_now

logger = structlog.get_logger("pulsar.rules")

DEFAULT_RULES_PATH = Path(__file__).parent / "default_rules.yml"

BUSINESS_HOURS_UTC = range(9, 17)


def _after_hours(event_value, _expected) -> bool:
    try:
        ts = datetime.fromisoformat(event_value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return ts.hour not in BUSINESS_HOURS_UTC


# Condition operators: (event value, rule value) -> matched. Unknown operators never match.
OPERATORS = {
    "eq": lambda v, want: v == want,
    "neq": lambda v, want: v != want,
    "contains": lambda v, want: isinstance(v, str) and want in v,
    "in": lambda v, want: v in (want if isinstance(want, list) else [want]),
    "after_hours": _after_hours,
}


def load_rules() -> list[dict]:
    return list(rules_collection.find({"enabled": True}))


def evaluate_event(event: dict, rules: list[dict] | None = None) -> list[dict]:
    """Evaluate a normalized event against alert rules. Pass `rules` to avoid reloading them per event."""
    triggered = []
    for rule in load_rules() if rules is None else rules:
        if not _matches(rule, event):
            continue
        if _is_duplicate(rule, event):
            logger.debug("Alert deduplicated", rule=rule.get("name"), event_id=event.get("id"))
            continue
        triggered.append(rule)
        _create_alert(rule, event)
    return triggered


def _matches(rule: dict, event: dict) -> bool:
    conditions = rule.get("conditions", [])
    if not conditions:
        return False
    for cond in conditions:
        operator = OPERATORS.get(cond.get("op", "eq"))
        event_value = _get_nested(event, cond.get("field") or "")
        if operator is None or not operator(event_value, cond.get("value")):
            return False
    return True


def _get_nested(obj: dict, path: str):
    val = obj
    for part in path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def _actor_of(event: dict) -> str:
    return event.get("actor_display") or event.get("actor_upn") or "unknown"


def _is_duplicate(rule: dict, event: dict) -> bool:
    """Check if an alert for this rule + actor was recently created."""
    if ALERT_DEDUPE_MINUTES <= 0:
        return False
    query = {
        "rule_id": str(rule.get("_id")),
        "actor": _actor_of(event),
        "timestamp": {"$gte": (utc_now() - timedelta(minutes=ALERT_DEDUPE_MINUTES)).isoformat()},
    }
    return alerts_collection.count_documents(query, limit=1) > 0


def _create_alert(rule: dict, event: dict):
    alert = {
        "timestamp": utc_now().isoformat(),
        "rule_id": str(rule.get("_id")),
        "rule_name": rule.get("name", "Unnamed rule"),
        "severity": rule.get("severity", "medium"),
        "event_id": event.get("id"),
        "event_dedupe_key": event.get("dedupe_key"),
        "service": event.get("service"),  # lets the alert list apply the same privacy filter as events
        "operation": event.get("operation"),
        "actor": _actor_of(event),
        "message": rule.get("message", f"Rule '{rule.get('name')}' triggered"),
        "status": "open",  # open | acknowledged | resolved | false_positive
    }
    try:
        alerts_collection.insert_one(alert)
        logger.info("Alert created", rule=rule.get("name"), event_id=event.get("id"))
    except Exception as exc:
        logger.warning("Failed to create alert", error=str(exc))
        return

    if ALERT_WEBHOOK_URL:
        try:
            send_notification(
                webhook_url=ALERT_WEBHOOK_URL,
                format_type=ALERT_WEBHOOK_FORMAT,
                rule_name=alert["rule_name"],
                severity=alert["severity"],
                message=rule.get("message", ""),
                event=event,
            )
        except Exception as exc:
            logger.warning("Failed to send notification", error=str(exc))


def seed_default_rules():
    """Insert the shipped rule templates that do not exist yet. Safe for concurrent startup.

    Existing rules are left alone, so an admin's edits (disabled, re-tuned) survive restarts.
    """
    defaults = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8")) or []
    inserted = 0
    for rule in defaults:
        try:
            result = rules_collection.update_one({"name": rule["name"]}, {"$setOnInsert": rule}, upsert=True)
            if result.upserted_id:
                inserted += 1
        except Exception as exc:
            logger.warning("Failed to seed rule", rule=rule["name"], error=str(exc))
    if inserted:
        logger.info("Default PULSAR rules seeded", inserted=inserted, total=len(defaults))
