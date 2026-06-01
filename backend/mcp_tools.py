"""Shared FastMCP instance and tool definitions for PULSAR.

Imported by both:
  - mcp_server.py  (stdio transport, local development)
  - routes/mcp.py  (SSE transport, mounted in FastAPI with OIDC auth)
"""

from mcp.server.fastmcp import FastMCP
from mcp_common import (
    handle_get_event,
    handle_get_summary,
    handle_search_events,
)

mcp = FastMCP("pulsar")


@mcp.tool()
async def search_events(
    entity: str = "",
    services: list[str] | None = None,
    operation: str = "",
    result: str = "",
    days: int = 7,
    limit: int = 20,
) -> str:
    """Search M365 audit events by entity, service, operation, or result.

    Args:
        entity: Device name, user UPN, or email to search for.
        services: Filter by service (e.g. Intune, Directory, Exchange).
        operation: Filter by operation name (partial match).
        result: Filter by result (e.g. success, failure).
        days: Number of days to look back (default 7).
        limit: Maximum number of events to return (default 20, max 100).
    """
    arguments = {
        "entity": entity,
        "days": days,
        "limit": limit,
    }
    if services:
        arguments["services"] = services
    if operation:
        arguments["operation"] = operation
    if result:
        arguments["result"] = result
    content = await handle_search_events(arguments)
    return content[0].text if content else "No events found."


@mcp.tool()
async def get_event(event_id: str) -> str:
    """Retrieve the full JSON detail of a single audit event by its ID.

    Args:
        event_id: The event ID to retrieve.
    """
    content = await handle_get_event({"event_id": event_id})
    return content[0].text if content else f"Event {event_id} not found."


@mcp.tool()
async def get_summary(days: int = 7) -> str:
    """Get an aggregated summary of audit activity: top services, operations, results, and actors.

    Args:
        days: Number of days to summarise (default 7).
    """
    content = await handle_get_summary({"days": days})
    return content[0].text if content else "No events in the specified period."
