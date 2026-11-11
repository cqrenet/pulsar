"""Ingestion pipeline: fetch every source, normalize, upsert, alert, forward."""

import time

import structlog
from config import settings
from database import events_collection
from graph.audit_logs import fetch_audit_logs
from metrics import track_fetch, track_fetch_duration, track_fetch_error
from models.event_model import normalize_event
from pymongo import UpdateOne
from rules import evaluate_event, load_rules
from siem import forward_events
from sources.intune_audit import fetch_intune_audit
from sources.unified_audit import fetch_unified_audit
from utils.common import iso_z, utc_now
from watermark import get_watermark, set_watermark

logger = structlog.get_logger("pulsar.ingest")

DEFAULT_FETCH_HOURS = 168
MAX_FETCH_HOURS = 720  # 30 days

# (watermark/metrics key, label for error messages, fetch function)
SOURCES = [
    ("directory", "Directory audit", fetch_audit_logs),
    ("unified", "Unified audit", fetch_unified_audit),
    ("intune", "Intune audit", fetch_intune_audit),
]


def _fetch_source(key: str, label: str, fn, window_hours: int, now: str, errors: list[str]) -> list[dict]:
    start_time = time.time()
    try:
        since = get_watermark(key)
        result = fn(since=since) if since else fn(hours=window_hours)
        set_watermark(key, now, status="healthy")
        track_fetch(key, len(result))
        return result
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        track_fetch_error(key)
        set_watermark(key, now, status="error")
        return []
    finally:
        track_fetch_duration(key, time.time() - start_time)


def _upsert(docs: list[dict]) -> None:
    ops = []
    for doc in docs:
        key = doc.get("dedupe_key")
        match = {"dedupe_key": key} if key else {"id": doc.get("id"), "timestamp": doc.get("timestamp")}
        ops.append(UpdateOne(match, {"$set": doc}, upsert=True))
    events_collection.bulk_write(ops, ordered=False)


def run_fetch(hours: int = DEFAULT_FETCH_HOURS):
    window = max(1, min(hours, MAX_FETCH_HOURS))
    now = iso_z(utc_now())
    errors: list[str] = []

    raw_events = []
    for key, label, fn in SOURCES:
        raw_events.extend(_fetch_source(key, label, fn, window, now, errors))

    normalized = [normalize_event(e) for e in raw_events]
    if normalized:
        _upsert(normalized)
        if settings.ALERTS_ENABLED:
            rules = load_rules()  # once per run, not once per event
            for doc in normalized:
                evaluate_event(doc, rules)
        forward_events(normalized)

    return {"stored_events": len(normalized), "errors": errors}
