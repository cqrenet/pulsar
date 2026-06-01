"""PULSAR MCP server — SSE transport mounted inside FastAPI with OIDC auth."""

import structlog
from auth import AUTH_ALLOWED_GROUPS, AUTH_ALLOWED_ROLES, AUTH_ENABLED, _allowed, _decode_token, _get_jwks
from mcp_tools import mcp
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

logger = structlog.get_logger("pulsar.mcp")


class _OidcAuthMiddleware(BaseHTTPMiddleware):
    """Validate Entra Bearer tokens before allowing MCP access."""

    async def dispatch(self, request: Request, call_next):
        if not AUTH_ENABLED:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return PlainTextResponse("Unauthorized", status_code=401)

        token = auth_header.split(" ", 1)[1]
        try:
            jwks = _get_jwks()
            claims = _decode_token(token, jwks)
        except Exception as exc:
            logger.warning("MCP auth failed", error=str(exc))
            return PlainTextResponse("Unauthorized", status_code=401)

        if not _allowed(claims, AUTH_ALLOWED_ROLES, AUTH_ALLOWED_GROUPS):
            logger.warning("MCP auth forbidden", sub=claims.get("sub"))
            return PlainTextResponse("Forbidden", status_code=403)

        logger.info("MCP SSE connection", sub=claims.get("sub", "anonymous"))
        return await call_next(request)


def build_mcp_app():
    """Return the FastMCP SSE ASGI app, wrapped with OIDC auth middleware."""
    app = mcp.sse_app()
    if AUTH_ENABLED:
        app.add_middleware(_OidcAuthMiddleware)
    return app


mcp_app = build_mcp_app()
