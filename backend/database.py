from contextlib import suppress

import structlog
from config import DB_NAME, MONGO_URI, RETENTION_DAYS
from pymongo import ASCENDING, DESCENDING, TEXT, MongoClient

client = MongoClient(MONGO_URI or "mongodb://localhost:27017")
db = client[DB_NAME]
events_collection = db["events"]
saved_searches_collection = db["saved_searches"]
alerts_collection = db["alerts"]
logger = structlog.get_logger("pulsar.database")


def _dedupe_alert_rules():
    """Remove duplicate alert_rules by name, keeping the oldest document."""
    try:
        pipeline = [
            {"$sort": {"_id": ASCENDING}},
            {"$group": {"_id": "$name", "first_id": {"$first": "$_id"}}},
        ]
        seen = {doc["_id"]: doc["first_id"] for doc in db["alert_rules"].aggregate(pipeline)}
        for name, keep_id in seen.items():
            db["alert_rules"].delete_many({"name": name, "_id": {"$ne": keep_id}})
    except Exception:
        pass  # Collection may not exist yet


def setup_indexes(max_retries: int = 5, delay: float = 2.0):
    """Ensure MongoDB indexes exist. Retries on connection errors."""
    from time import sleep

    for attempt in range(1, max_retries + 1):
        try:
            events_collection.create_index("dedupe_key", unique=True, sparse=True)
            events_collection.create_index([("timestamp", DESCENDING)])
            events_collection.create_index([("service", ASCENDING), ("timestamp", DESCENDING)])
            events_collection.create_index("id")
            events_collection.create_index("correlation_id", sparse=True)
            saved_searches_collection.create_index([("created_by", ASCENDING), ("created_at", DESCENDING)])
            _dedupe_alert_rules()
            db["alert_rules"].create_index("name", unique=True)
            events_collection.create_index(
                [("actor_display", TEXT), ("raw_text", TEXT), ("operation", TEXT)],
                name="text_search_index",
            )
            if RETENTION_DAYS > 0:
                events_collection.create_index(
                    [("timestamp", ASCENDING)],
                    expireAfterSeconds=RETENTION_DAYS * 24 * 60 * 60,
                    name="ttl_timestamp",
                )
            else:
                with suppress(Exception):
                    events_collection.drop_index("ttl_timestamp")
            logger.info("MongoDB indexes ensured")
            return
        except Exception as exc:
            if attempt == max_retries:
                logger.error("Failed to ensure MongoDB indexes", error=str(exc))
                raise
            logger.warning("MongoDB not ready, retrying...", attempt=attempt, error=str(exc))
            sleep(delay)
