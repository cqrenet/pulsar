"""Pluggable notification channels for PULSAR alerts.

Supported channels:
- webhook: POST JSON to any URL (Slack, Teams, generic)
"""

import re
from datetime import UTC, datetime
from typing import NamedTuple

import requests
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from utils.ssrf import validate_outbound_url

logger = structlog.get_logger("pulsar.notifications")

WEBHOOK_TIMEOUT = 15


def _defang(value) -> str:
    """Neutralise tenant-controlled text (display names, targets) before it reaches a chat channel.

    Anyone who can rename a device or create a group controls these strings. Without this, a name like
    "[Verify your account](https://evil.example)" becomes a clickable link posted by the trusted PULSAR bot.
    URLs are defanged the way SOC tools do it (hxxps[:]//), and markdown link syntax is broken.
    """
    text = re.sub(r"(?i)\bhttp(s?)://", r"hxxp\1[:]//", str(value))
    text = re.sub(r"(?i)\bwww\.", "www[.]", text)
    return text.replace("](", "] (")


def _slack_text(value) -> str:
    # Slack treats <...> as control sequences (links, @channel); its docs require escaping & < >.
    return _defang(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class EventFields(NamedTuple):
    ts: str
    op: str
    actor: str
    targets: str
    svc: str


def _event_fields(event: dict, clean) -> EventFields:
    """Event-derived card fields (all tenant-controlled), cleaned for the target channel."""
    return EventFields(
        ts=clean(event.get("timestamp", "?")),
        op=clean(event.get("operation", "unknown")),
        actor=clean(event.get("actor_display", "unknown")),
        targets=clean(", ".join(event.get("target_displays", [])) or "—"),
        svc=clean(event.get("service", "unknown")),
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _post_webhook(url: str, payload: dict) -> requests.Response:
    """POST to webhook with retry on connection/timeout errors."""
    return requests.post(url, json=payload, timeout=WEBHOOK_TIMEOUT, headers={"Content-Type": "application/json"})


def _build_slack_payload(rule_name: str, severity: str, message: str, event: dict) -> dict:
    """Build a Slack-compatible block payload."""
    color = {"high": "#ef4444", "medium": "#f97316", "low": "#3b82f6"}.get(severity, "#94a3b8")
    f = _event_fields(event, _slack_text)
    return {
        "text": f"[{severity.upper()}] {rule_name}: {message}",
        "attachments": [
            {
                "color": color,
                "fields": [
                    {"title": "Rule", "value": rule_name, "short": True},
                    {"title": "Severity", "value": severity.upper(), "short": True},
                    {"title": "Service", "value": f.svc, "short": True},
                    {"title": "Action", "value": f.op, "short": True},
                    {"title": "Actor", "value": f.actor, "short": True},
                    {"title": "Target", "value": f.targets, "short": True},
                    {"title": "Time", "value": f.ts, "short": False},
                ],
                "footer": "PULSAR",
            }
        ],
    }


def _build_teams_payload(rule_name: str, severity: str, message: str, event: dict) -> dict:
    """Build a Microsoft Teams adaptive card payload."""
    color = {"high": "Attention", "medium": "Warning", "low": "Good"}.get(severity, "Default")
    # Adaptive Cards render markdown in FactSet values, so every event field is defanged.
    f = _event_fields(event, _defang)
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"🚨 {severity.upper()}: {rule_name}",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": color,
                        },
                        {"type": "TextBlock", "text": message, "wrap": True},
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Service:", "value": f.svc},
                                {"title": "Action:", "value": f.op},
                                {"title": "Actor:", "value": f.actor},
                                {"title": "Target:", "value": f.targets},
                                {"title": "Time:", "value": f.ts},
                            ],
                        },
                    ],
                },
            }
        ],
    }


def _build_generic_payload(rule_name: str, severity: str, message: str, event: dict) -> dict:
    """Build a generic JSON payload."""
    return {
        "alert": {
            "rule_name": rule_name,
            "severity": severity,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        "event": {
            "id": event.get("id"),
            "timestamp": event.get("timestamp"),
            "service": event.get("service"),
            "operation": event.get("operation"),
            "actor_display": event.get("actor_display"),
            "target_displays": event.get("target_displays"),
            "result": event.get("result"),
        },
    }


def send_notification(
    webhook_url: str,
    format_type: str,
    rule_name: str,
    severity: str,
    message: str,
    event: dict,
) -> bool:
    """Send an alert notification to the configured channel.

    Args:
        webhook_url: URL to POST to.
        format_type: "slack", "teams", or "generic".
        rule_name: Name of the triggered rule.
        severity: high, medium, or low.
        message: Human-readable alert message.
        event: The normalized event that triggered the alert.

    Returns:
        True if delivery succeeded, False otherwise.
    """
    if not webhook_url:
        return False

    try:
        validate_outbound_url(webhook_url)
    except ValueError as exc:
        logger.warning("Notification blocked: invalid webhook URL", error=str(exc))
        return False

    builders = {
        "slack": _build_slack_payload,
        "teams": _build_teams_payload,
        "generic": _build_generic_payload,
    }
    builder = builders.get(format_type, _build_generic_payload)
    payload = builder(rule_name, severity, message, event)

    try:
        res = _post_webhook(webhook_url, payload)
        res.raise_for_status()
        logger.info(
            "Notification sent",
            rule=rule_name,
            severity=severity,
            format=format_type,
            status_code=res.status_code,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Notification failed after retries",
            rule=rule_name,
            severity=severity,
            format=format_type,
            error=str(exc),
        )
        return False
