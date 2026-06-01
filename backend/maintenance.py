"""
Maintenance utilities for existing audit events.

Run re-normalization (including Graph enrichment) over stored events to populate
new display fields. Example:

    python maintenance.py renormalize --limit 500
"""

import argparse

from database import events_collection
from graph.audit_logs import _enrich_events
from graph.auth import get_access_token
from models.event_model import _make_dedupe_key, normalize_event
from pymongo import UpdateOne


def renormalize(limit: int = None, batch_size: int = 200) -> int:
    """
    Re-run enrichment + normalization on stored events using the latest mapping.
    Returns the number of documents updated.
    """
    token = get_access_token()

    cursor = events_collection.find({}, projection={"raw": 1})
    if limit:
        cursor = cursor.limit(int(limit))

    updated = 0
    batch: list[UpdateOne] = []

    for doc in cursor:
        raw = doc.get("raw") or {}
        enriched = _enrich_events([raw], token)[0]
        normalized = normalize_event(enriched)
        # Preserve original _id
        normalized.pop("_id", None)

        batch.append(UpdateOne({"_id": doc["_id"]}, {"$set": normalized}))
        if len(batch) >= batch_size:
            events_collection.bulk_write(batch, ordered=False)
            updated += len(batch)
            batch = []

    if batch:
        events_collection.bulk_write(batch, ordered=False)
        updated += len(batch)

    return updated


def dedupe(limit: int = None, batch_size: int = 500) -> int:
    """
    Remove duplicate events based on dedupe_key. Keeps the first occurrence encountered.
    """
    cursor = events_collection.find({}, projection={"_id": 1, "dedupe_key": 1, "raw": 1, "id": 1, "timestamp": 1}).sort(
        "timestamp", 1
    )
    if limit:
        cursor = cursor.limit(int(limit))

    seen: set[str] = set()
    to_delete = []
    processed = 0

    for doc in cursor:
        key = doc.get("dedupe_key") or _make_dedupe_key(doc.get("raw") or doc)
        if not key:
            continue
        if key in seen:
            to_delete.append(doc["_id"])
        else:
            seen.add(key)
        processed += 1
        if len(to_delete) >= batch_size:
            events_collection.delete_many({"_id": {"$in": to_delete}})
            to_delete = []

    if to_delete:
        events_collection.delete_many({"_id": {"$in": to_delete}})

    removed = processed - len(seen)
    return removed if removed > 0 else 0


def main():
    parser = argparse.ArgumentParser(description="Maintenance tasks")
    sub = parser.add_subparsers(dest="command")

    rn = sub.add_parser("renormalize", help="Re-run enrichment/normalization on stored events")
    rn.add_argument("--limit", type=int, default=None, help="Limit number of events to process")

    dd = sub.add_parser("dedupe", help="Remove duplicate events based on dedupe_key")
    dd.add_argument("--limit", type=int, default=None, help="Limit number of events to scan (for testing)")

    args = parser.parse_args()
    if args.command == "renormalize":
        count = renormalize(limit=args.limit)
        print(f"Renormalized {count} events")
    elif args.command == "dedupe":
        removed = dedupe(limit=args.limit)
        print(f"Removed {removed} duplicate documents")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
