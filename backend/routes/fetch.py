import time

import structlog
from audit_trail import log_action
from auth import require_auth
from config import ALERTS_ENABLED
from database import events_collection
from fastapi import APIRouter, Depends, HTTPException, Query
from graph.audit_logs import fetch_audit_logs
from metrics import track_fetch, track_fetch_duration, track_fetch_error
from models.api import FetchAuditLogsResponse
from models.event_model import normalize_event
from pymongo import UpdateOne
from siem import forward_event
from sources.intune_audit import fetch_intune_audit
from sources.unified_audit import fetch_unified_audit
from watermark import get_watermark, set_watermark

logger = structlog.get_logger("pulsar.fetch")

router = APIRouter(dependencies=[Depends(require_auth)])


def run_fetch(hours: int = 168):
    from datetime import datetime

    window = max(1, min(hours, 720))  # cap to 30 days for sanity
    now = datetime.utcnow().isoformat() + "Z"
    logs = []
    errors = []

    def fetch_source(fn, label, source_key):
        start_time = time.time()
        try:
            since = get_watermark(source_key)
            result = fn(since=since) if since else fn(hours=window)
            set_watermark(source_key, now, status="healthy")
            track_fetch(source_key, len(result))
            return result
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            track_fetch_error(source_key)
            set_watermark(source_key, now, status="error")
            return []
        finally:
            track_fetch_duration(source_key, time.time() - start_time)

    logs.extend(fetch_source(fetch_audit_logs, "Directory audit", "directory"))
    logs.extend(fetch_source(fetch_unified_audit, "Unified audit", "unified"))
    logs.extend(fetch_source(fetch_intune_audit, "Intune audit", "intune"))

    normalized = [normalize_event(e) for e in logs]
    if normalized:
        ops = []
        for doc in normalized:
            key = doc.get("dedupe_key")
            if key:
                ops.append(UpdateOne({"dedupe_key": key}, {"$set": doc}, upsert=True))
            else:
                ops.append(
                    UpdateOne({"id": doc.get("id"), "timestamp": doc.get("timestamp")}, {"$set": doc}, upsert=True)
                )
        events_collection.bulk_write(ops, ordered=False)

        if ALERTS_ENABLED:
            from rules import evaluate_event

            for doc in normalized:
                evaluate_event(doc)

        for doc in normalized:
            forward_event(doc)

    return {"stored_events": len(normalized), "errors": errors}


@router.get("/fetch-audit-logs", response_model=FetchAuditLogsResponse)
async def fetch_logs(
    hours: int = Query(default=168, ge=1, le=720),
    user: dict = Depends(require_auth),
):
    import asyncio

    try:
        result = await asyncio.to_thread(run_fetch, hours=hours)
        log_action(
            "fetch_audit_logs",
            "/api/fetch-audit-logs",
            {"hours": hours, "stored": result["stored_events"]},
            user.get("sub", "anonymous"),
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Fetch failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to fetch audit logs") from exc
