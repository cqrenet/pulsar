"""Shared FastMCP instance and tool definitions for PULSAR.

Imported by both:
  - mcp_server.py  (stdio transport, local development)
  - routes/mcp.py  (SSE transport, mounted in FastAPI with OIDC auth)
"""

from audit_trail import log_action
from config import MCP_ALLOWED_HOSTS
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp_common import (
    handle_get_event,
    handle_get_summary,
    handle_search_events,
)
from utils.common import user_sub

# DNS rebinding protection — keep enabled but allow the configured public hostname.
# Without this, FastMCP defaults host to 127.0.0.1 and only allows loopback Host
# headers, rejecting any request that arrives via a reverse proxy with the real
# public hostname (e.g. pulsar.cqre.net).
#
# MCP_ALLOWED_HOSTS: comma-separated list of host[:port] values to allow in addition
# to the standard loopback entries.  Typically the public hostname of your PULSAR
# instance.  Defaults to empty (loopback only — fine for stdio / local use).
_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *MCP_ALLOWED_HOSTS]

mcp = FastMCP(
    "pulsar",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
    ),
)


def _caller(ctx: Context, tool: str, arguments: dict) -> dict:
    """Identity set by the SSE auth middleware, audit-logged per tool call.

    stdio has no HTTP request and no identity, so it gets no roles: privacy-gated data stays hidden there too.
    """
    request = ctx.request_context.request
    user = getattr(request.state, "user", None) if request is not None else None
    user = user or {"sub": "mcp-stdio" if request is None else "anonymous"}
    log_action(f"mcp_{tool}", "/mcp", arguments, user_sub(user, "unknown"))
    return user


@mcp.tool()
async def search_events(
    ctx: Context,
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
    content = await handle_search_events(arguments, _caller(ctx, "search_events", arguments))
    return content[0].text if content else "No events found."


@mcp.tool()
async def get_event(ctx: Context, event_id: str) -> str:
    """Retrieve the full JSON detail of a single audit event by its ID.

    Args:
        event_id: The event ID to retrieve.
    """
    arguments = {"event_id": event_id}
    content = await handle_get_event(arguments, _caller(ctx, "get_event", arguments))
    return content[0].text if content else f"Event {event_id} not found."


@mcp.tool()
async def get_summary(ctx: Context, days: int = 7) -> str:
    """Get an aggregated summary of audit activity: top services, operations, results, and actors.

    Args:
        days: Number of days to summarise (default 7).
    """
    arguments = {"days": days}
    content = await handle_get_summary(arguments, _caller(ctx, "get_summary", arguments))
    return content[0].text if content else "No events in the specified period."
