import asyncio

import structlog
from audit_trail import log_action
from auth import require_admin, require_auth
from fastapi import APIRouter, Depends, HTTPException, Query
from ingest import DEFAULT_FETCH_HOURS, MAX_FETCH_HOURS, run_fetch
from models.api import FetchAuditLogsResponse
from utils.common import user_sub

logger = structlog.get_logger("pulsar.fetch")

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/fetch-audit-logs", response_model=FetchAuditLogsResponse)
async def fetch_logs(
    hours: int = Query(default=DEFAULT_FETCH_HOURS, ge=1, le=MAX_FETCH_HOURS),
    user: dict = Depends(require_admin),
):
    try:
        result = await asyncio.to_thread(run_fetch, hours=hours)
        log_action(
            "fetch_audit_logs",
            "/api/fetch-audit-logs",
            {"hours": hours, "stored": result["stored_events"]},
            user_sub(user),
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Fetch failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to fetch audit logs") from exc
