from database import db

watermarks_collection = db["watermarks"]


def get_watermark(source: str) -> str | None:
    """Return the ISO timestamp of the last successful fetch for a source."""
    doc = watermarks_collection.find_one({"source": source})
    return doc.get("last_fetch_time") if doc else None


def set_watermark(source: str, timestamp: str, status: str | None = None):
    """Persist the latest fetch attempt timestamp and optional status for a source."""
    doc: dict = {"last_attempt_time": timestamp}
    if status == "healthy":
        doc["last_fetch_time"] = timestamp
    if status:
        doc["status"] = status
    watermarks_collection.update_one(
        {"source": source},
        {"$set": doc},
        upsert=True,
    )
