"""Aggregated audit summary endpoint — used by AURORA and the PULSAR UI."""

from auth import apply_privacy_filter, require_auth
from fastapi import APIRouter, Depends, Query
from queries import summarize_events
from utils.common import days_ago_iso

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/summary")
def get_summary(days: int = Query(default=7, ge=1, le=90), user: dict = Depends(require_auth)):
    """Return aggregated audit activity for the last N days.

    Includes counts by service, operation, result, and top actors.
    Used by AURORA for cross-tool correlation and by the PULSAR dashboard.
    """
    query = apply_privacy_filter({"timestamp": {"$gte": days_ago_iso(days)}}, user)
    return {"days": days, **summarize_events(query)}
