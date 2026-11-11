from auth import require_auth
from fastapi import APIRouter, Depends
from ingest import SOURCES
from models.api import SourceHealthResponse
from watermark import watermarks_collection

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/source-health", response_model=list[SourceHealthResponse])
def source_health():
    """Return the last known fetch status for each ingestion source."""
    results = []
    for source, _label, _fn in SOURCES:
        doc = watermarks_collection.find_one({"source": source}) or {}
        last_fetch = doc.get("last_fetch_time")
        results.append(
            {
                "source": source,
                "last_fetch_time": last_fetch,
                "last_attempt_time": doc.get("last_attempt_time"),
                "status": doc.get("status") or ("healthy" if last_fetch else "unknown"),
            }
        )
    return results
