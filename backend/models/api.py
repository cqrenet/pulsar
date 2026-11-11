from typing import Annotated, Literal

from pydantic import BaseModel, Field


class PaginatedEventResponse(BaseModel):
    items: list[dict]
    total: int
    page_size: int
    next_cursor: str | None = None


class FilterOptionsResponse(BaseModel):
    services: list[str]
    operations: list[str]
    results: list[str]
    actors: list[str]
    actor_upns: list[str]
    devices: list[str]


class FetchAuditLogsResponse(BaseModel):
    stored_events: int
    errors: list[str]


class SourceHealthResponse(BaseModel):
    source: str
    last_fetch_time: str | None = None
    last_attempt_time: str | None = None
    status: str


Tag = Annotated[str, Field(max_length=100)]


class TagsUpdateRequest(BaseModel):
    tags: list[Tag] = Field(..., max_length=50)


class BulkTagsRequest(BaseModel):
    tags: list[Tag] = Field(..., max_length=50)
    mode: Literal["append", "replace"] = "append"


class CommentAddRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class AlertCondition(BaseModel):
    field: str = Field(..., max_length=100)
    op: Literal["eq", "neq", "contains", "in", "after_hours"]
    value: str | list[str] | None = None


class AlertRule(BaseModel):
    id: str | None = None
    name: str = Field(..., max_length=200)
    enabled: bool
    severity: Literal["high", "medium", "low"]
    conditions: list[AlertCondition] = Field(..., max_length=20)
    message: str = Field(..., max_length=1000)
