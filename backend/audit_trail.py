from datetime import UTC, datetime

import structlog
from database import db

logger = structlog.get_logger("pulsar.audit")
audit_collection = db["pulsar_audit"]


def log_action(action: str, resource: str, details: dict | None = None, user: str | None = None):
    """Log an action in the PULSAR audit trail."""
    doc = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "resource": resource,
        "details": details or {},
        "user": user or "anonymous",
    }
    try:
        audit_collection.insert_one(doc)
    except Exception as exc:
        logger.warning("Failed to write audit trail", error=str(exc))
