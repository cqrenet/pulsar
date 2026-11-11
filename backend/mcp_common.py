"""Shared MCP tool handlers used by both stdio and SSE transports.

pymongo is blocking, so each handler runs its query in a worker thread.
"""

import asyncio
import json

from auth import apply_privacy_filter
from database import events_collection
from mcp.types import TextContent
from queries import EventFilters, summarize_events
from utils.common import contains, days_ago_iso


def _clamp(value, low: int, high: int, default: int) -> int:
    """Bound numeric tool arguments. Note limit=0 means "no limit" to MongoDB, so it must never pass through."""
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _text(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _search_events(arguments: dict, claims: dict | None) -> list[TextContent]:
    limit = _clamp(arguments.get("limit", 20), 1, 100, 20)
    # Free-text arguments are matched literally (see utils.common.contains).
    filters = EventFilters.plain(
        services=[str(s) for s in arguments.get("services") or []] or None,
        operation=arguments.get("operation") or None,
        result=arguments.get("result") or None,
        start=days_ago_iso(_clamp(arguments.get("days", 7), 1, 365, 7)),
    )
    query = filters.to_query()

    entity = arguments.get("entity")
    if entity:
        query = {
            "$and": [
                query,
                {
                    "$or": [
                        {"target_displays": {"$elemMatch": contains(entity)}},
                        {"actor_display": contains(entity)},
                        {"actor_upn": contains(entity)},
                        {"raw_text": contains(entity)},
                    ]
                },
            ]
        }

    events = list(events_collection.find(apply_privacy_filter(query, claims)).sort("timestamp", -1).limit(limit))
    if not events:
        return _text("No matching events found.")

    lines = [f"Found {len(events)} event(s):\n"]
    for e in events:
        ts = e.get("timestamp", "?")[:16].replace("T", " ")
        lines.append(
            f"{ts} | {e.get('service', '?')} | {e.get('operation', '?')} | {e.get('actor_display', '?')} | {e.get('result', '?')}"
        )
    return _text("\n".join(lines))


def _get_event(arguments: dict, claims: dict | None) -> list[TextContent]:
    event_id = str(arguments["event_id"])
    event = events_collection.find_one(apply_privacy_filter({"id": event_id}, claims))
    if not event:
        return _text(f"Event {event_id} not found.")
    event.pop("_id", None)
    return _text(json.dumps(event, indent=2, default=str))


def _get_summary(arguments: dict, claims: dict | None) -> list[TextContent]:
    days = _clamp(arguments.get("days", 7), 1, 365, 7)
    summary = summarize_events(apply_privacy_filter({"timestamp": {"$gte": days_ago_iso(days)}}, claims))
    if summary["total"] == 0:
        return _text("No events in the specified period.")

    lines = [f"Summary for the last {days} days ({summary['total']} total events)\n"]
    for title, key in (
        ("By service", "by_service"),
        ("By action", "by_operation"),
        ("By result", "by_result"),
        ("Top actors", "top_actors"),
    ):
        lines.append(f"{'' if title == 'By service' else chr(10)}{title}:")
        lines.extend(f"  {row['name']}: {row['count']}" for row in summary[key])
    return _text("\n".join(lines))


async def handle_search_events(arguments: dict, claims: dict | None) -> list[TextContent]:
    return await asyncio.to_thread(_search_events, arguments, claims)


async def handle_get_event(arguments: dict, claims: dict | None) -> list[TextContent]:
    return await asyncio.to_thread(_get_event, arguments, claims)


async def handle_get_summary(arguments: dict, claims: dict | None) -> list[TextContent]:
    return await asyncio.to_thread(_get_summary, arguments, claims)
