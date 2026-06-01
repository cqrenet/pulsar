import base64
import re
from datetime import UTC, datetime

from audit_trail import log_action
from auth import require_auth, user_can_access_privacy_services
from bson import ObjectId
from config import PRIVACY_SENSITIVE_OPERATIONS, PRIVACY_SERVICES
from database import events_collection
from fastapi import APIRouter, Depends, HTTPException, Query
from models.api import (
    BulkTagsRequest,
    CommentAddRequest,
    FilterOptionsResponse,
    PaginatedEventResponse,
    TagsUpdateRequest,
)

router = APIRouter(dependencies=[Depends(require_auth)])


def _encode_cursor(timestamp: str, oid: str) -> str:
    payload = f"{timestamp}|{oid}"
    return base64.b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        payload = base64.b64decode(cursor.encode()).decode()
        timestamp, oid = payload.split("|", 1)
        return timestamp, oid
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


def _build_query(
    service: str | None = None,
    services: list[str] | None = None,
    actor: str | None = None,
    operation: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
    search: str | None = None,
    cursor: str | None = None,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    exclude_operations: list[str] | None = None,
) -> dict:
    filters = []

    if service:
        filters.append({"service": service})
    if services:
        filters.append({"service": {"$in": services}})
    if exclude_operations:
        filters.append({"operation": {"$nin": exclude_operations}})
    if actor:
        actor_safe = re.escape(actor)
        filters.append(
            {
                "$or": [
                    {"actor_display": {"$regex": actor_safe, "$options": "i"}},
                    {"actor_upn": {"$regex": actor_safe, "$options": "i"}},
                    {"actor.user.userPrincipalName": {"$regex": actor_safe, "$options": "i"}},
                    {"actor.user.id": actor},
                ]
            }
        )
    if operation:
        filters.append({"operation": {"$regex": re.escape(operation), "$options": "i"}})
    if result:
        filters.append({"result": {"$regex": re.escape(result), "$options": "i"}})
    if start or end:
        time_filter = {}
        if start:
            time_filter["$gte"] = start
        if end:
            time_filter["$lte"] = end
        filters.append({"timestamp": time_filter})
    if search:
        search_safe = re.escape(search)
        filters.append(
            {
                "$or": [
                    {"raw_text": {"$regex": search_safe, "$options": "i"}},
                    {"display_summary": {"$regex": search_safe, "$options": "i"}},
                    {"actor_display": {"$regex": search_safe, "$options": "i"}},
                    {"target_displays": {"$elemMatch": {"$regex": search_safe, "$options": "i"}}},
                    {"operation": {"$regex": search_safe, "$options": "i"}},
                ]
            }
        )
    if include_tags:
        filters.append({"tags": {"$all": include_tags}})
    if exclude_tags:
        filters.append({"tags": {"$not": {"$all": exclude_tags}}})

    if cursor:
        try:
            cursor_ts, cursor_oid = _decode_cursor(cursor)
        except HTTPException:
            raise
        filters.append(
            {
                "$or": [
                    {"timestamp": {"$lt": cursor_ts}},
                    {"timestamp": cursor_ts, "_id": {"$lt": ObjectId(cursor_oid)}},
                ]
            }
        )

    return {"$and": filters} if filters else {}


@router.get("/events", response_model=PaginatedEventResponse)
def list_events(
    service: str | None = None,
    services: list[str] | None = Query(default=None),
    actor: str | None = None,
    operation: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
    search: str | None = None,
    cursor: str | None = None,
    page_size: int = Query(default=50, ge=1, le=500),
    include_tags: list[str] | None = Query(default=None),
    exclude_tags: list[str] | None = Query(default=None),
    user: dict = Depends(require_auth),
):
    privacy_excluded_services = [] if user_can_access_privacy_services(user) else list(PRIVACY_SERVICES)
    privacy_excluded_ops = [] if user_can_access_privacy_services(user) else list(PRIVACY_SENSITIVE_OPERATIONS)
    query = _build_query(
        service=service,
        services=services,
        actor=actor,
        operation=operation,
        result=result,
        start=start,
        end=end,
        search=search,
        cursor=cursor,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        exclude_operations=privacy_excluded_ops,
    )
    if privacy_excluded_services:
        query = query if query else {}
        if "$and" not in query:
            query = {"$and": [query]} if query else {"$and": []}
        query["$and"].append({"service": {"$nin": privacy_excluded_services}})

    safe_page_size = max(1, min(page_size, 500))

    try:
        total = events_collection.count_documents(query) if not cursor else -1
        cursor_query = events_collection.find(query).sort([("timestamp", -1), ("_id", -1)]).limit(safe_page_size)
        events = list(cursor_query)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to query events") from exc

    next_cursor = None
    if len(events) == safe_page_size:
        last = events[-1]
        next_cursor = _encode_cursor(last["timestamp"], str(last["_id"]))

    for e in events:
        e["_id"] = str(e["_id"])

    log_action(
        "list_events",
        "/api/events",
        {
            "filters": {
                k: v
                for k, v in {
                    "service": service,
                    "actor": actor,
                    "operation": operation,
                    "result": result,
                    "start": start,
                    "end": end,
                    "search": search,
                    "cursor": cursor,
                    "page_size": page_size,
                }.items()
                if v is not None
            }
        },
        user.get("sub", "anonymous"),
    )

    return {
        "items": events,
        "total": total,
        "page_size": safe_page_size,
        "next_cursor": next_cursor,
    }


@router.post("/events/bulk-tags")
def bulk_tags(
    body: BulkTagsRequest,
    service: str | None = None,
    services: list[str] | None = Query(default=None),
    actor: str | None = None,
    operation: str | None = None,
    result: str | None = None,
    start: str | None = None,
    end: str | None = None,
    search: str | None = None,
    include_tags: list[str] | None = Query(default=None),
    exclude_tags: list[str] | None = Query(default=None),
    user: dict = Depends(require_auth),
):
    privacy_excluded_services = [] if user_can_access_privacy_services(user) else list(PRIVACY_SERVICES)
    privacy_excluded_ops = [] if user_can_access_privacy_services(user) else list(PRIVACY_SENSITIVE_OPERATIONS)
    query = _build_query(
        service=service,
        services=services,
        actor=actor,
        operation=operation,
        result=result,
        start=start,
        end=end,
        search=search,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        exclude_operations=privacy_excluded_ops,
    )
    if privacy_excluded_services:
        query = query if query else {}
        if "$and" not in query:
            query = {"$and": [query]} if query else {"$and": []}
        query["$and"].append({"service": {"$nin": privacy_excluded_services}})
    tags = [t.strip() for t in body.tags if t.strip()]
    if not tags:
        raise HTTPException(status_code=400, detail="No tags provided")

    update = {"$set": {"tags": tags}} if body.mode == "replace" else {"$addToSet": {"tags": {"$each": tags}}}

    try:
        matched = events_collection.count_documents(query, limit=10001)
        if matched > 10000:
            raise HTTPException(
                status_code=400,
                detail="Bulk tag update matches too many events (>10000). Narrow your filters.",
            )
        result_obj = events_collection.update_many(query, update)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to update tags") from exc

    log_action(
        "bulk_tags",
        "/api/events/bulk-tags",
        {"tags": tags, "mode": body.mode, "matched": result_obj.matched_count},
        user.get("sub", "anonymous"),
    )
    return {"matched": result_obj.matched_count, "modified": result_obj.modified_count}


@router.get("/filter-options", response_model=FilterOptionsResponse)
def filter_options(
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require_auth),
):
    safe_limit = max(1, min(limit, 1000))
    try:
        services = sorted(events_collection.distinct("service"))[:safe_limit]
        operations = sorted(events_collection.distinct("operation"))[:safe_limit]
        results = sorted([r for r in events_collection.distinct("result") if r])[:safe_limit]
        actors = sorted([a for a in events_collection.distinct("actor_display") if a])[:safe_limit]
        actor_upns = sorted([a for a in events_collection.distinct("actor_upn") if a])[:safe_limit]
        devices = sorted([a for a in events_collection.distinct("target_displays") if isinstance(a, str)])[:safe_limit]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load filter options") from exc

    if not user_can_access_privacy_services(user):
        services = [s for s in services if s not in PRIVACY_SERVICES]
        operations = [o for o in operations if o not in PRIVACY_SENSITIVE_OPERATIONS]

    return {
        "services": services,
        "operations": operations,
        "results": results,
        "actors": actors,
        "actor_upns": actor_upns,
        "devices": devices,
    }


@router.patch("/events/{event_id}/tags")
def update_tags(
    event_id: str,
    body: TagsUpdateRequest,
    user: dict = Depends(require_auth),
):
    result = events_collection.update_one({"id": event_id}, {"$set": {"tags": body.tags}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    log_action("update_tags", f"/api/events/{event_id}/tags", {"tags": body.tags}, user.get("sub", "anonymous"))
    return {"tags": body.tags}


@router.post("/events/{event_id}/comments")
def add_comment(
    event_id: str,
    body: CommentAddRequest,
    user: dict = Depends(require_auth),
):
    comment = {
        "text": body.text,
        "author": user.get("sub", "anonymous"),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    result = events_collection.update_one({"id": event_id}, {"$push": {"comments": comment}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    log_action("add_comment", f"/api/events/{event_id}/comments", {"text": body.text}, user.get("sub", "anonymous"))
    return comment
