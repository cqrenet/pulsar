"""CRUD for saved filter searches (bookmarks)."""

import uuid

import structlog
from auth import require_auth
from database import saved_searches_collection
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from utils.common import iso_z, user_sub, utc_now

router = APIRouter(dependencies=[Depends(require_auth)])
logger = structlog.get_logger("pulsar.saved_searches")

MAX_SAVED_SEARCHES_PER_USER = 50


class SavedSearchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    filters: dict = Field(default_factory=dict)


@router.get("/saved-searches")
def list_saved_searches(user: dict = Depends(require_auth)):
    """Return saved searches for the current user."""
    sub = user_sub(user)
    cursor = saved_searches_collection.find({"created_by": sub}).sort("created_at", -1)
    items = []
    for doc in cursor:
        doc["id"] = doc.pop("_id")
        items.append(doc)
    return items


@router.post("/saved-searches")
def create_saved_search(body: SavedSearchCreate, user: dict = Depends(require_auth)):
    """Save the current filter set."""
    sub = user_sub(user)
    existing = saved_searches_collection.count_documents({"created_by": sub})
    if existing >= MAX_SAVED_SEARCHES_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {MAX_SAVED_SEARCHES_PER_USER} saved searches per user reached.",
        )

    doc = {
        "_id": str(uuid.uuid4()),
        "name": body.name,
        "filters": body.filters,
        "created_at": iso_z(utc_now()),
        "created_by": sub,
    }
    saved_searches_collection.insert_one(doc)
    logger.info("Saved search created", name=body.name, user=sub)
    doc["id"] = doc.pop("_id")
    return doc


@router.delete("/saved-searches/{search_id}")
def delete_saved_search(search_id: str, user: dict = Depends(require_auth)):
    """Delete a saved search (only if owned by current user)."""
    sub = user_sub(user)
    result = saved_searches_collection.delete_one({"_id": search_id, "created_by": sub})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Saved search not found")
    logger.info("Saved search deleted", search_id=search_id, user=sub)
    return {"status": "deleted"}
