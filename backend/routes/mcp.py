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

import hmac

import structlog
from auth import verify_bearer
from config import settings
from fastapi import HTTPException
from mcp.server.transport_security import TransportSecuritySettings
from mcp_tools import mcp

logger = structlog.get_logger("pulsar.mcp")

_UNAUTHORIZED = b"Unauthorized"
_FORBIDDEN = b"Forbidden"


async def _reject(send, status: int, body: bytes) -> None:
    """Send a minimal plain-text error response."""
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

    async def _forward(self, user: dict, scope, receive, send) -> None:
        # Tools read the caller from request.state.user to apply privacy filtering and audit logging.
        scope.setdefault("state", {})["user"] = user
        await self.app(scope, receive, send)

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
        if settings.MCP_API_KEY:
            if hmac.compare_digest(token.encode(), settings.MCP_API_KEY.encode()):
                logger.info("MCP SSE connection via API key")
                # A shared key carries no roles, so privacy-gated data stays hidden from it.
                await self._forward({"sub": "mcp-api-key"}, scope, receive, send)
                return
            # Key is configured but didn't match — fall through to OIDC only if enabled,
            # otherwise reject (avoids leaking that a key is configured).
            if not settings.AUTH_ENABLED:
                logger.warning("MCP auth failed: invalid API key")
                await _reject(send, 401, _UNAUTHORIZED)
                return

        # 2. Entra JWT path
        if settings.AUTH_ENABLED:
            if not token:
                await _reject(send, 401, _UNAUTHORIZED)
                return
            try:
                claims = await verify_bearer(token)
            except HTTPException as exc:
                logger.warning("MCP OIDC auth failed", status=exc.status_code, detail=exc.detail)
                await _reject(send, exc.status_code, _FORBIDDEN if exc.status_code == 403 else _UNAUTHORIZED)
                return
            except Exception as exc:
                logger.warning("MCP OIDC auth failed", error=str(exc))
                await _reject(send, 401, _UNAUTHORIZED)
                return
            logger.info("MCP SSE connection via Entra JWT", sub=claims.get("sub", "unknown"))
            await self._forward(claims, scope, receive, send)
            return

        # No auth configured — open access (warn once at startup, handled in main.py)
        await self._forward({"sub": "anonymous"}, scope, receive, send)


def build_mcp_app():
    """Return the MCP SSE ASGI app, wrapped with the auth middleware.

    DNS rebinding protection stays on, with the configured public hostname allowed. Without MCP_ALLOWED_HOSTS the
    transport accepts only loopback Host headers and rejects requests arriving through a reverse proxy.
    MCP_ALLOWED_HOSTS: comma-separated host[:port] values accepted in addition to the loopback entries.
    """
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *settings.MCP_ALLOWED_HOSTS],
    )
    app = mcp.sse_app(transport_security=security)
    return _McpAuthMiddleware(app)


mcp_app = build_mcp_app()
