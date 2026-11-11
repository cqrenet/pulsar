from datetime import UTC, datetime

import structlog
from config import AUDIT_DB_NAME
from database import client

logger = structlog.get_logger("pulsar.audit")
# Own database: the app's MongoDB user may only insert and read here, never update or delete.
audit_collection = client[AUDIT_DB_NAME]["pulsar_audit"]


def log_action(action: str, resource: str, details: dict | None = None, user: str | None = None):
    """Log an action in the PULSAR audit trail."""
    doc = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "resource": resource,
        "details": details or {},
        "user": user or "anonymous",
    }
    # Copy to stdout first: container logs keep the record even if the database is tampered with.
    logger.info("audit", audit=dict(doc))
    try:
        audit_collection.insert_one(doc)
    except Exception as exc:
        logger.warning("Failed to write audit trail", error=str(exc))
