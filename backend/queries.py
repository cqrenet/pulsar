"""Event query building shared by the REST API and the MCP tools."""

import base64

from bson import ObjectId
from database import events_collection
from fastapi import HTTPException, Query
from utils.common import contains

QUERY_MAX_TIME_MS = 5_000


def _count_documents(query: dict) -> int:
    try:
        return events_collection.count_documents(query, maxTimeMS=QUERY_MAX_TIME_MS)
    except TypeError:
        # mongomock and older test doubles may not expose PyMongo's timeout keyword.
        return events_collection.count_documents(query)


def _aggregate(pipeline: list[dict]):
    try:
        return events_collection.aggregate(pipeline, maxTimeMS=QUERY_MAX_TIME_MS)
    except TypeError:
        # mongomock and older test doubles may not expose PyMongo's timeout keyword.
        return events_collection.aggregate(pipeline)


def encode_cursor(timestamp: str, oid: str) -> str:
    return base64.b64encode(f"{timestamp}|{oid}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, ObjectId]:
    try:
        timestamp, oid = base64.b64decode(cursor.encode()).decode().split("|", 1)
        return timestamp, ObjectId(oid)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


class EventFilters:
    """The event filter set, read from query parameters via `Depends()` and turned into a Mongo query."""

    def __init__(
        self,
        service: str | None = None,
        services: list[str] | None = Query(default=None),
        actor: str | None = None,
        operation: str | None = None,
        result: str | None = None,
        start: str | None = None,
        end: str | None = None,
        search: str | None = None,
        correlation_id: str | None = None,
        include_tags: list[str] | None = Query(default=None),
        exclude_tags: list[str] | None = Query(default=None),
    ):
        self.service = service
        self.services = services
        self.actor = actor
        self.operation = operation
        self.result = result
        self.start = start
        self.end = end
        self.search = search
        self.correlation_id = correlation_id
        self.include_tags = include_tags
        self.exclude_tags = exclude_tags

    @classmethod
    def plain(cls, **fields) -> "EventFilters":
        """Build outside a request (e.g. MCP tools), where FastAPI's Query() defaults do not resolve."""
        return cls(**{"services": None, "include_tags": None, "exclude_tags": None, **fields})

    def audit_details(self) -> dict:
        """The filters that were set, for the audit trail."""
        return {k: v for k, v in vars(self).items() if v is not None}

    def to_query(self, cursor: str | None = None) -> dict:
        filters = []
        if self.correlation_id:
            filters.append({"correlation_id": self.correlation_id})
        if self.service:
            filters.append({"service": self.service})
        if self.services:
            filters.append({"service": {"$in": self.services}})
        if self.actor:
            filters.append(
                {
                    "$or": [
                        {"actor_display": contains(self.actor)},
                        {"actor_upn": contains(self.actor)},
                        {"actor.user.userPrincipalName": contains(self.actor)},
                        {"actor.user.id": self.actor},
                    ]
                }
            )
        if self.operation:
            filters.append({"operation": contains(self.operation)})
        if self.result:
            filters.append({"result": contains(self.result)})
        if self.start or self.end:
            filters.append(
                {
                    "timestamp": {
                        **({"$gte": self.start} if self.start else {}),
                        **({"$lte": self.end} if self.end else {}),
                    }
                }
            )
        if self.search:
            filters.append(
                {
                    "$or": [
                        {"raw_text": contains(self.search)},
                        {"display_summary": contains(self.search)},
                        {"actor_display": contains(self.search)},
                        {"target_displays": {"$elemMatch": contains(self.search)}},
                        {"operation": contains(self.search)},
                    ]
                }
            )
        if self.include_tags:
            filters.append({"tags": {"$all": self.include_tags}})
        if self.exclude_tags:
            filters.append({"tags": {"$not": {"$all": self.exclude_tags}}})
        if cursor:
            cursor_ts, cursor_oid = decode_cursor(cursor)
            filters.append(
                {"$or": [{"timestamp": {"$lt": cursor_ts}}, {"timestamp": cursor_ts, "_id": {"$lt": cursor_oid}}]}
            )
        return {"$and": filters} if filters else {}


def summarize_events(query: dict) -> dict:
    """Counts by service, operation, result and top actors for the events matching `query`."""
    total = _count_documents(query)
    if total == 0:
        return {"total": 0, "by_service": [], "by_operation": [], "by_result": [], "top_actors": []}

    def group_by(field: str, limit: int = 10) -> list[dict]:
        pipeline = [
            {"$match": query},
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ]
        return [{"name": r["_id"] or "Unknown", "count": r["count"]} for r in _aggregate(pipeline)]

    return {
        "total": total,
        "by_service": group_by("service"),
        "by_operation": group_by("operation"),
        "by_result": group_by("result", limit=5),
        "top_actors": group_by("actor_display"),
    }
