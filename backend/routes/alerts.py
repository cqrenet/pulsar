"""Alert management endpoints."""

import re
from typing import Literal

from auth import require_auth
from bson import ObjectId
from database import alerts_collection
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

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
):
    query = {}
    if status:
        query["status"] = status
    if severity:
        query["severity"] = severity
    if rule_name:
        query["rule_name"] = {"$regex": re.escape(rule_name), "$options": "i"}

    total = alerts_collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = alerts_collection.find(query, {"_id": 0}).sort("timestamp", -1).skip(skip).limit(page_size)
    return {"items": list(cursor), "total": total}


@router.patch("/alerts/{alert_id}/status")
def update_alert_status(alert_id: str, body: AlertStatusUpdate):
    result = alerts_collection.update_one(
        {"_id": ObjectId(alert_id)},
        {"$set": {"status": body.status}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"updated": True, "status": body.status}


@router.get("/alerts/summary")
def alert_summary():
    """Return counts by status and severity for the dashboard."""
    pipeline = [
        {
            "$group": {
                "_id": {"status": "$status", "severity": "$severity"},
                "count": {"$sum": 1},
            }
        }
    ]
    by_status_severity = list(alerts_collection.aggregate(pipeline))

    total_open = alerts_collection.count_documents({"status": "open"})
    total_acknowledged = alerts_collection.count_documents({"status": "acknowledged"})
    total_resolved = alerts_collection.count_documents({"status": "resolved"})
    total_false_positive = alerts_collection.count_documents({"status": "false_positive"})

    return {
        "total_open": total_open,
        "total_acknowledged": total_acknowledged,
        "total_resolved": total_resolved,
        "total_false_positive": total_false_positive,
        "by_status_severity": by_status_severity,
    }
