"""Shared MCP tool handlers used by both stdio and SSE transports."""

import json
from datetime import UTC, datetime, timedelta

from database import events_collection
from mcp.types import TextContent


async def handle_search_events(arguments: dict) -> list[TextContent]:
    days = arguments.get("days", 7)
    limit = min(arguments.get("limit", 20), 100)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")

    filters = [{"timestamp": {"$gte": since}}]

    services = arguments.get("services")
    if services:
        filters.append({"service": {"$in": services}})

    operation = arguments.get("operation")
    if operation:
        filters.append({"operation": {"$regex": operation, "$options": "i"}})

    result = arguments.get("result")
    if result:
        filters.append({"result": {"$regex": result, "$options": "i"}})

    entity = arguments.get("entity")
    if entity:
        entity_safe = entity.replace(".", "\\.").replace("(", "\\(").replace(")", "\\)")
        filters.append(
            {
                "$or": [
                    {"target_displays": {"$elemMatch": {"$regex": entity_safe, "$options": "i"}}},
                    {"actor_display": {"$regex": entity_safe, "$options": "i"}},
                    {"actor_upn": {"$regex": entity_safe, "$options": "i"}},
                    {"raw_text": {"$regex": entity_safe, "$options": "i"}},
                ]
            }
        )

    query = {"$and": filters}
    cursor = events_collection.find(query).sort("timestamp", -1).limit(limit)
    events = list(cursor)

    if not events:
        return [TextContent(type="text", text="No matching events found.")]

    lines = [f"Found {len(events)} event(s):\n"]
    for e in events:
        ts = e.get("timestamp", "?")[:16].replace("T", " ")
        svc = e.get("service", "?")
        op = e.get("operation", "?")
        actor = e.get("actor_display", "?")
        result_str = e.get("result", "?")
        lines.append(f"{ts} | {svc} | {op} | {actor} | {result_str}")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_get_event(arguments: dict) -> list[TextContent]:
    event_id = arguments["event_id"]
    event = events_collection.find_one({"id": event_id})
    if not event:
        return [TextContent(type="text", text=f"Event {event_id} not found.")]
    event.pop("_id", None)
    return [TextContent(type="text", text=json.dumps(event, indent=2, default=str))]


async def handle_get_summary(arguments: dict) -> list[TextContent]:
    days = arguments.get("days", 7)
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    query = {"timestamp": {"$gte": since}}

    total = events_collection.count_documents(query)
    if total == 0:
        return [TextContent(type="text", text="No events in the specified period.")]

    svc_pipeline = [
        {"$match": query},
        {"$group": {"_id": "$service", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    op_pipeline = [
        {"$match": query},
        {"$group": {"_id": "$operation", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]
    result_pipeline = [
        {"$match": query},
        {"$group": {"_id": "$result", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    actor_pipeline = [
        {"$match": query},
        {"$group": {"_id": "$actor_display", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ]

    svc_counts = list(events_collection.aggregate(svc_pipeline))
    op_counts = list(events_collection.aggregate(op_pipeline))
    result_counts = list(events_collection.aggregate(result_pipeline))
    actor_counts = list(events_collection.aggregate(actor_pipeline))

    lines = [f"Summary for the last {days} days ({total} total events)\n"]

    lines.append("By service:")
    for row in svc_counts:
        lines.append(f"  {row['_id'] or 'Unknown'}: {row['count']}")

    lines.append("\nBy action:")
    for row in op_counts:
        lines.append(f"  {row['_id'] or 'Unknown'}: {row['count']}")

    lines.append("\nBy result:")
    for row in result_counts:
        lines.append(f"  {row['_id'] or 'Unknown'}: {row['count']}")

    lines.append("\nTop actors:")
    for row in actor_counts:
        lines.append(f"  {row['_id'] or 'Unknown'}: {row['count']}")

    return [TextContent(type="text", text="\n".join(lines))]


# JSON schemas for tool definitions
SEARCH_EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "entity": {"type": "string", "description": "Device name, user UPN, or email to search for"},
        "services": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Filter by service (e.g. Intune, Directory, Exchange)",
        },
        "operation": {"type": "string", "description": "Filter by operation name"},
        "result": {"type": "string", "description": "Filter by result (success, failure)"},
        "days": {"type": "integer", "description": "Number of days to look back (default 7)"},
        "limit": {"type": "integer", "description": "Max events to return (default 20)"},
    },
}

GET_EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "event_id": {"type": "string", "description": "The event ID to retrieve"},
    },
    "required": ["event_id"],
}

GET_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "days": {"type": "integer", "description": "Number of days to summarise (default 7)"},
    },
}
