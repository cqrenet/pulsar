from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EventItem(BaseModel):
    id: str | None = None
    timestamp: str | None = None
    service: str | None = None
    operation: str | None = None
    result: str | None = None
    actor_display: str | None = None
    target_displays: list[str] | None = None
    display_summary: str | None = None
    display_category: str | None = None
    dedupe_key: str | None = None
    actor: dict | None = None
    targets: list[dict] | None = None
    raw: dict | None = None
    raw_text: str | None = None
    tags: list[str] | None = None
    comments: list[dict] | None = None

    model_config = ConfigDict(extra="allow")


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


class TagsUpdateRequest(BaseModel):
    tags: list[str] = Field(..., max_length=50)


class BulkTagsRequest(BaseModel):
    tags: list[str] = Field(..., max_length=50)
    mode: Literal["append", "replace"] = "append"


class CommentAddRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class AlertCondition(BaseModel):
    field: str = Field(..., max_length=100)
    op: Literal["eq", "neq", "contains", "in", "after_hours", "threshold_count"]
    value: str | list[str] | None = None


class AlertRuleResponse(BaseModel):
    id: str | None = None
    name: str = Field(..., max_length=200)
    enabled: bool
    severity: Literal["high", "medium", "low"]
    conditions: list[AlertCondition] = Field(..., max_length=20)
    message: str = Field(..., max_length=1000)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    services: list[str] | None = None
    actor: str | None = None
    operation: str | None = None
    result: str | None = None
    start: str | None = None
    end: str | None = None
    include_tags: list[str] | None = None
    exclude_tags: list[str] | None = None
    async_mode: bool = False  # enqueue async job instead of waiting


class AskEventRef(BaseModel):
    id: str | None = None
    timestamp: str | None = None
    operation: str | None = None
    actor_display: str | None = None
    target_displays: list[str] | None = None
    display_summary: str | None = None
    service: str | None = None
    result: str | None = None


class AskResponse(BaseModel):
    answer: str
    events: list[AskEventRef]
    query_info: dict
    llm_used: bool
    llm_error: str | None = None
    job_id: str | None = None
