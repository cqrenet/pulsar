"""Alert management endpoints."""

from typing import Literal

from audit_trail import set_audit
from auth import apply_privacy_filter, require_auth
from bson import ObjectId
from database import alerts_collection
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from utils.common import contains

router = APIRouter(dependencies=[Depends(require_auth)])


class AlertStatusUpdate(BaseModel):
    status: Literal["open", "acknowledged", "resolved", "false_positive"]


class AlertListResponse(BaseModel):
    items: list[dict]
    total: int


@router.get("/alerts", response_model=AlertListResponse)
def list_alerts(
    status: str = Query(default="", description="Filter by status"),
    severity: str = Query(default="", description="Filter by severity"),
    rule_name: str = Query(default="", description="Filter by rule name"),
    page_size: int = Query(default=50, ge=1, le=200),
    page: int = Query(default=1, ge=1),
    user: dict = Depends(require_auth),
):
    query = {}
    if status:
        query["status"] = status
    if severity:
        query["severity"] = severity
    if rule_name:
        query["rule_name"] = contains(rule_name)
    query = apply_privacy_filter(query, user)

    total = alerts_collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = alerts_collection.find(query, {"_id": 0}).sort("timestamp", -1).skip(skip).limit(page_size)
    return {"items": list(cursor), "total": total}


@router.patch("/alerts/{alert_id}/status")
def update_alert_status(request: Request, alert_id: str, body: AlertStatusUpdate, user: dict = Depends(require_auth)):
    if not ObjectId.is_valid(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    result = alerts_collection.update_one(
        apply_privacy_filter({"_id": ObjectId(alert_id)}, user),
        {"$set": {"status": body.status}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    set_audit(request, "update_alert_status", {"alert_id": alert_id, "status": body.status})
    return {"updated": True, "status": body.status}


@router.get("/alerts/summary")
def alert_summary(user: dict = Depends(require_auth)):
    """Return counts by status and severity for the dashboard (privacy-filtered like the alert list)."""
    pipeline = [
        {"$match": apply_privacy_filter({}, user)},
        {"$group": {"_id": {"status": "$status", "severity": "$severity"}, "count": {"$sum": 1}}},
    ]
    by_status_severity = list(alerts_collection.aggregate(pipeline))
    by_status: dict[str, int] = {}
    for row in by_status_severity:
        status = row["_id"]["status"]
        by_status[status] = by_status.get(status, 0) + row["count"]

    return {
        "total_open": by_status.get("open", 0),
        "total_acknowledged": by_status.get("acknowledged", 0),
        "total_resolved": by_status.get("resolved", 0),
        "total_false_positive": by_status.get("false_positive", 0),
        "by_status_severity": by_status_severity,
    }
