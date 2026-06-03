"""PULSAR MCP server — SSE transport mounted inside FastAPI with auth middleware.

Auth priority (checked in order):
  1. API key  — if MCP_API_KEY is set, a matching Bearer token or x-api-key header
               is accepted immediately, bypassing OIDC.  Used by Claude Desktop and
               AURORA (service-to-service).
  2. Entra JWT — if AUTH_ENABLED is true and no API key matched, validates the Bearer
               token as an Entra ID access token.

Using a raw ASGI middleware class (not BaseHTTPMiddleware) intentionally: Starlette's
BaseHTTPMiddleware buffers the entire response body before forwarding, which silently
breaks SSE (an infinite streaming response).  The raw class passes scope/receive/send
through unmodified and never buffers anything.
"""

import structlog
from auth import AUTH_ALLOWED_GROUPS, AUTH_ALLOWED_ROLES, AUTH_ENABLED, _allowed, _decode_token, _get_jwks_async
from config import MCP_API_KEY
from mcp_tools import mcp

logger = structlog.get_logger("pulsar.mcp")

_UNAUTHORIZED = b"Unauthorized"
_FORBIDDEN = b"Forbidden"


def _reject(status: int, body: bytes):
    """Return a minimal ASGI send sequence for a plain-text error response."""

    async def _send_rejection(send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    [b"content-type", b"text/plain; charset=utf-8"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return _send_rejection


class _McpAuthMiddleware:
    """Pure ASGI auth gate for the MCP SSE endpoint.

    Accepts requests that satisfy at least one of:
    - API key match  (MCP_API_KEY set and token equals the key)
    - Valid Entra JWT (AUTH_ENABLED=true and token passes OIDC validation)

    If neither MCP_API_KEY nor AUTH_ENABLED is configured the server runs open
    (development / internal-only deployments).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract bearer token (supports both Authorization: Bearer and x-api-key)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode()
        key_header = headers.get(b"x-api-key", b"").decode()
        token = key_header or (auth[7:] if auth.lower().startswith("bearer ") else "")

        # 1. API key path — fast, no Entra round-trip
        if MCP_API_KEY:
            if token == MCP_API_KEY:
                logger.info("MCP SSE connection via API key")
                await self.app(scope, receive, send)
                return
            # Key is configured but didn't match — fall through to OIDC only if enabled,
            # otherwise reject (avoids leaking that a key is configured).
            if not AUTH_ENABLED:
                logger.warning("MCP auth failed: invalid API key")
                await _reject(401, _UNAUTHORIZED)(send)
                return

        # 2. Entra JWT path
        if AUTH_ENABLED:
            if not token:
                await _reject(401, _UNAUTHORIZED)(send)
                return
            try:
                jwks = await _get_jwks_async()
                claims = _decode_token(token, jwks)
            except Exception as exc:
                logger.warning("MCP OIDC auth failed", error=str(exc))
                await _reject(401, _UNAUTHORIZED)(send)
                return
            if not _allowed(claims, AUTH_ALLOWED_ROLES, AUTH_ALLOWED_GROUPS):
                logger.warning("MCP OIDC auth forbidden", sub=claims.get("sub"))
                await _reject(403, _FORBIDDEN)(send)
                return
            logger.info("MCP SSE connection via Entra JWT", sub=claims.get("sub", "unknown"))
            await self.app(scope, receive, send)
            return

        # No auth configured — open access (warn once at startup, handled in main.py)
        await self.app(scope, receive, send)


def build_mcp_app():
    """Return the FastMCP SSE ASGI app, wrapped with the auth middleware."""
    app = mcp.sse_app()
    return _McpAuthMiddleware(app)


mcp_app = build_mcp_app()
