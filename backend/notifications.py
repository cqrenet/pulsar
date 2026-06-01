"""Pluggable notification channels for PULSAR alerts.

Supported channels:
- webhook: POST JSON to any URL (Slack, Teams, generic)
"""

import ipaddress
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = structlog.get_logger("pulsar.notifications")

WEBHOOK_TIMEOUT = 15


def _validate_webhook_url(url: str):
    """Prevent SSRF by rejecting internal/reserved addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Webhook URL scheme '{parsed.scheme}' is not allowed")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Webhook URL must have a valid hostname")
    blocked = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}
    if hostname in blocked:
        raise ValueError(f"Webhook URL hostname '{hostname}' is not allowed")
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Webhook URL IP '{hostname}' is not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise


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
    ts = event.get("timestamp", "?")
    op = event.get("operation", "unknown")
    actor = event.get("actor_display", "unknown")
    targets = ", ".join(event.get("target_displays", [])) or "—"
    svc = event.get("service", "unknown")
    return {
        "text": f"[{severity.upper()}] {rule_name}: {message}",
        "attachments": [
            {
                "color": color,
                "fields": [
                    {"title": "Rule", "value": rule_name, "short": True},
                    {"title": "Severity", "value": severity.upper(), "short": True},
                    {"title": "Service", "value": svc, "short": True},
                    {"title": "Action", "value": op, "short": True},
                    {"title": "Actor", "value": actor, "short": True},
                    {"title": "Target", "value": targets, "short": True},
                    {"title": "Time", "value": ts, "short": False},
                ],
                "footer": "PULSAR",
            }
        ],
    }


def _build_teams_payload(rule_name: str, severity: str, message: str, event: dict) -> dict:
    """Build a Microsoft Teams adaptive card payload."""
    color = {"high": "Attention", "medium": "Warning", "low": "Good"}.get(severity, "Default")
    ts = event.get("timestamp", "?")
    op = event.get("operation", "unknown")
    actor = event.get("actor_display", "unknown")
    targets = ", ".join(event.get("target_displays", [])) or "—"
    svc = event.get("service", "unknown")
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
                                {"title": "Service:", "value": svc},
                                {"title": "Action:", "value": op},
                                {"title": "Actor:", "value": actor},
                                {"title": "Target:", "value": targets},
                                {"title": "Time:", "value": ts},
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
        _validate_webhook_url(webhook_url)
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
