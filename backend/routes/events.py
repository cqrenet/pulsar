from audit_trail import log_action, set_audit
from auth import apply_privacy_filter, require_auth
from database import events_collection
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models.api import (
    BulkTagsRequest,
    CommentAddRequest,
    FilterOptionsResponse,
    PaginatedEventResponse,
    TagsUpdateRequest,
)
from queries import EventFilters, encode_cursor
from utils.common import user_sub, utc_now

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/events", response_model=PaginatedEventResponse)
def list_events(
    filters: EventFilters = Depends(),
    cursor: str | None = None,
    page_size: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(require_auth),
):
    query = apply_privacy_filter(filters.to_query(cursor), user)

    try:
        total = events_collection.count_documents(query) if not cursor else -1
        events = list(events_collection.find(query).sort([("timestamp", -1), ("_id", -1)]).limit(page_size))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to query events") from exc

    next_cursor = None
    if len(events) == page_size:
        last = events[-1]
        next_cursor = encode_cursor(last["timestamp"], str(last["_id"]))

    for e in events:
        e["_id"] = str(e["_id"])

    log_action(
        "list_events",
        "/api/events",
        {"filters": {**filters.audit_details(), "cursor": cursor, "page_size": page_size}},
        user_sub(user),
    )

    return {"items": events, "total": total, "page_size": page_size, "next_cursor": next_cursor}


@router.post("/events/bulk-tags")
def bulk_tags(
    request: Request,
    body: BulkTagsRequest,
    filters: EventFilters = Depends(),
    user: dict = Depends(require_auth),
):
    query = apply_privacy_filter(filters.to_query(), user)
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

    set_audit(request, "bulk_tags", {"tags": tags, "mode": body.mode, "matched": result_obj.matched_count})
    return {"matched": result_obj.matched_count, "modified": result_obj.modified_count}


@router.get("/filter-options", response_model=FilterOptionsResponse)
def filter_options(
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require_auth),
):
    # Filter at the source: actors, UPNs and devices from hidden events would otherwise leak too.
    visible = apply_privacy_filter({}, user)
    try:
        services = sorted(events_collection.distinct("service", visible))[:limit]
        operations = sorted(events_collection.distinct("operation", visible))[:limit]
        results = sorted([r for r in events_collection.distinct("result", visible) if r])[:limit]
        actors = sorted([a for a in events_collection.distinct("actor_display", visible) if a])[:limit]
        actor_upns = sorted([a for a in events_collection.distinct("actor_upn", visible) if a])[:limit]
        devices = sorted([a for a in events_collection.distinct("target_displays", visible) if isinstance(a, str)])[
            :limit
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to load filter options") from exc

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
    request: Request,
    event_id: str,
    body: TagsUpdateRequest,
    user: dict = Depends(require_auth),
):
    result = events_collection.update_one(apply_privacy_filter({"id": event_id}, user), {"$set": {"tags": body.tags}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    set_audit(request, "update_tags", {"event_id": event_id, "tags": body.tags})
    return {"tags": body.tags}


@router.post("/events/{event_id}/comments")
def add_comment(
    request: Request,
    event_id: str,
    body: CommentAddRequest,
    user: dict = Depends(require_auth),
):
    comment = {
        "text": body.text,
        "author": user_sub(user),
        "timestamp": utc_now().isoformat(),
    }
    result = events_collection.update_one(
        apply_privacy_filter({"id": event_id}, user), {"$push": {"comments": comment}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    set_audit(request, "add_comment", {"event_id": event_id, "text": body.text})
    return comment
