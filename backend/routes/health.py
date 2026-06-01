from auth import require_auth
from fastapi import APIRouter, Depends
from models.api import SourceHealthResponse
from watermark import watermarks_collection

router = APIRouter(dependencies=[Depends(require_auth)])

SOURCES = ["directory", "unified", "intune"]


@router.get("/source-health", response_model=list[SourceHealthResponse])
def source_health():
    """Return the last known fetch status for each ingestion source."""
    results = []
    for source in SOURCES:
        doc = watermarks_collection.find_one({"source": source})
        if doc:
            status = doc.get("status")
            if not status:
                status = "healthy" if doc.get("last_fetch_time") else "unknown"
            results.append(
                {
                    "source": source,
                    "last_fetch_time": doc.get("last_fetch_time"),
                    "last_attempt_time": doc.get("last_attempt_time"),
                    "status": status,
                }
            )
        else:
            results.append(
                {
                    "source": source,
                    "last_fetch_time": None,
                    "last_attempt_time": None,
                    "status": "unknown",
                }
            )
    return results
