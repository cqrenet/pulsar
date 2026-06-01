"""Aggregated audit summary endpoint — used by AURORA and the PULSAR UI."""

from datetime import UTC, datetime, timedelta

import structlog
from auth import require_auth
from database import events_collection
from fastapi import APIRouter, Depends, Query

router = APIRouter(dependencies=[Depends(require_auth)])
logger = structlog.get_logger("pulsar.summary")


@router.get("/summary")
async def get_summary(days: int = Query(default=7, ge=1, le=90)):
    """Return aggregated audit activity for the last N days.

    Includes counts by service, operation, result, and top actors.
    Used by AURORA for cross-tool correlation and by the PULSAR dashboard.
    """
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    query = {"timestamp": {"$gte": since}}

    total = events_collection.count_documents(query)

    if total == 0:
        return {"days": days, "total": 0, "by_service": [], "by_operation": [], "by_result": [], "top_actors": []}

    def _agg(group_field: str, limit: int = 10) -> list[dict]:
        pipeline = [
            {"$match": query},
            {"$group": {"_id": f"${group_field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ]
        return [{"name": r["_id"] or "Unknown", "count": r["count"]} for r in events_collection.aggregate(pipeline)]

    return {
        "days": days,
        "total": total,
        "by_service": _agg("service"),
        "by_operation": _agg("operation"),
        "by_result": _agg("result", limit=5),
        "top_actors": _agg("actor_display"),
    }
