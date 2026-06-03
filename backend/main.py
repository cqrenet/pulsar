import asyncio
import ipaddress
import logging
import os
import time
from contextlib import suppress
from pathlib import Path

import structlog
from audit_trail import log_action
from config import (
    AUTH_ALLOWED_GROUPS,
    AUTH_ALLOWED_ROLES,
    AUTH_CLIENT_ID,
    AUTH_ENABLED,
    AUTH_TENANT_ID,
    CORS_ORIGINS,
    DOCS_ENABLED,
    ENABLE_PERIODIC_FETCH,
    FETCH_INTERVAL_MINUTES,
    MCP_API_KEY,
    MCP_CLIENT_ID,
    METRICS_ALLOWED_IPS,
    WEBHOOK_CLIENT_SECRET,
)
from database import setup_indexes
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from metrics import observe_request, prometheus_metrics
from middleware import CorrelationIdMiddleware
from routes.alerts import router as alerts_router
from routes.config import router as config_router
from routes.events import router as events_router
from routes.fetch import router as fetch_router
from routes.fetch import run_fetch
from routes.health import router as health_router
from routes.mcp import mcp_app
from routes.rules import router as rules_router
from routes.saved_searches import router as saved_searches_router
from routes.summary import router as summary_router
from routes.webhooks import router as webhooks_router


def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", level=logging.INFO)


configure_logging()
logger = structlog.get_logger("pulsar.fetcher")

# Disable OpenAPI docs in production by default
app = FastAPI(
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

# CORS: when auth is enabled, never allow credentials with wildcard origins
_effective_cors = CORS_ORIGINS
_cors_credentials = True
if AUTH_ENABLED and "*" in _effective_cors:
    logger.warning(
        "CORS wildcard (*) is insecure with AUTH_ENABLED=true and allow_credentials. "
        "Disabling credentials. Set CORS_ORIGINS to your actual origin(s)."
    )
    _cors_credentials = False

app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_effective_cors,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    path = getattr(request.scope.get("route"), "path", request.url.path)
    observe_request(request.method, path, response.status_code, duration)
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    # Prevent caching of HTML and API responses by default
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    # Basic CSP for the UI and API (allows MSAL auth flows)
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval' cdn.jsdelivr.net alcdn.msauth.net; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' https://login.microsoftonline.com; "
            "frame-src 'self' https://login.microsoftonline.com; "
            "form-action 'self' https://login.microsoftonline.com; "
            "img-src 'self' data:; "
            "font-src 'self' data:;"
        )
    # Additional security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply Redis-backed rate limiting before processing the request."""
    # Exempt config and health endpoints from rate limiting
    exempt_paths = {"/api/config/auth", "/api/config/features", "/health", "/metrics"}
    if request.url.path.startswith("/api/") and request.url.path not in exempt_paths:
        from rate_limiter import check_rate_limit

        await check_rate_limit(request)
    return await call_next(request)


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/") and request.method in ("POST", "PATCH", "PUT", "DELETE"):
        user = "anonymous"
        if AUTH_ENABLED:
            from auth import _auth_context

            claims = _auth_context.get(None)
            if isinstance(claims, dict):
                user = claims.get("sub", "unknown")
        log_action(
            action=request.method.lower(),
            resource=request.url.path,
            details={"status_code": response.status_code},
            user=user,
        )
    return response


app.include_router(fetch_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(summary_router, prefix="/api")
app.include_router(config_router, prefix="/api")
app.include_router(webhooks_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(saved_searches_router, prefix="/api")
app.include_router(rules_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.mount("/mcp", mcp_app)


# ---------------------------------------------------------------------------
# OAuth 2.0 authorization server metadata (RFC 8414)
# Enables Claude Desktop, AURORA, and other MCP clients to discover Entra auth.
# Exposed at the server root so MCP clients can find it regardless of where
# the /mcp mount lives.  Requires AUTH_TENANT_ID (or TENANT_ID) + MCP_CLIENT_ID.
# ---------------------------------------------------------------------------
_oauth_client_id = MCP_CLIENT_ID or AUTH_CLIENT_ID
_oauth_tenant_id = AUTH_TENANT_ID

if _oauth_tenant_id and _oauth_client_id:
    from fastapi.responses import JSONResponse as _JSONResponse

    _oauth_base = f"https://login.microsoftonline.com/{_oauth_tenant_id}"
    _oauth_metadata = {
        "issuer": f"{_oauth_base}/v2.0",
        "authorization_endpoint": f"{_oauth_base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{_oauth_base}/oauth2/v2.0/token",
        "scopes_supported": [f"api://{_oauth_client_id}/user_impersonation", "openid", "profile", "offline_access"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def oauth_authorization_server_metadata():
        return _JSONResponse(_oauth_metadata)


@app.get("/health")
async def health_check():
    from database import db

    try:
        db.command("ping")
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        logger.error("Health check failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


def _client_ip(request: Request) -> str:
    """Best-effort client IP: X-Forwarded-For first hop, or direct client host."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _is_metrics_allowed(ip: str) -> bool:
    """Check if IP is in the configured metrics allowlist."""
    if not METRICS_ALLOWED_IPS:
        return True
    try:
        client_addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for network in METRICS_ALLOWED_IPS.split(","):
        network = network.strip()
        if not network:
            continue
        try:
            if client_addr in ipaddress.ip_network(network, strict=False):
                return True
        except ValueError:
            continue
    return False


@app.get("/metrics")
async def metrics(request: Request):
    client_ip = _client_ip(request)
    if not _is_metrics_allowed(client_ip):
        raise HTTPException(status_code=403, detail="Forbidden")
    return Response(content=prometheus_metrics(), media_type="text/plain")


@app.get("/api/version")
async def version():
    return {"version": os.environ.get("VERSION", "unknown")}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Return generic error messages for unhandled exceptions to avoid info leakage."""
    if isinstance(exc, HTTPException):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None) or {},
        )
    logger.error("Unhandled exception", path=request.url.path, error=str(exc))
    return Response(
        content='{"detail":"Internal server error"}',
        status_code=500,
        media_type="application/json",
    )


frontend_dir = Path(__file__).parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")


async def _periodic_fetch():
    while True:
        try:
            await asyncio.to_thread(run_fetch)
            logger.info("Periodic fetch completed.")
        except Exception as exc:
            logger.error("Periodic fetch failed", error=str(exc))
        await asyncio.sleep(FETCH_INTERVAL_MINUTES * 60)


@app.on_event("startup")
async def start_periodic_fetch():
    setup_indexes()
    from rules import seed_default_rules

    seed_default_rules()
    logger.info(
        "PULSAR startup",
        version=os.environ.get("VERSION", "unknown"),
        auth_enabled=AUTH_ENABLED,
        mcp_api_key_set=bool(MCP_API_KEY),
        oauth_discovery=bool(_oauth_tenant_id and _oauth_client_id),
    )
    if not AUTH_ENABLED and not MCP_API_KEY:
        logger.warning(
            "MCP endpoint is open: AUTH_ENABLED=false and MCP_API_KEY is not set. "
            "Anyone who can reach /mcp can query audit events. "
            "Set MCP_API_KEY for a simple API key gate, or AUTH_ENABLED=true for Entra ID auth."
        )
    if _oauth_tenant_id and _oauth_client_id:
        logger.info(
            "OAuth discovery endpoint active",
            path="/.well-known/oauth-authorization-server",
            tenant=_oauth_tenant_id,
            client=_oauth_client_id,
        )
    # Warn when auth is enabled but no role/group restrictions are configured
    if AUTH_ENABLED and not AUTH_ALLOWED_ROLES and not AUTH_ALLOWED_GROUPS:
        logger.warning(
            "AUTH_ENABLED is true but no AUTH_ALLOWED_ROLES or AUTH_ALLOWED_GROUPS are configured. "
            "Any Entra user in the tenant can authenticate and access PULSAR. "
            "Set AUTH_ALLOWED_ROLES or AUTH_ALLOWED_GROUPS to restrict access."
        )
    if not WEBHOOK_CLIENT_SECRET:
        logger.warning(
            "WEBHOOK_CLIENT_SECRET is not set. Graph webhook notifications will be accepted without "
            "clientState validation, allowing any HTTP client to spoof Graph notifications. "
            "Set WEBHOOK_CLIENT_SECRET to the clientState used when creating Graph subscriptions."
        )
    if ENABLE_PERIODIC_FETCH:
        app.state.fetch_task = asyncio.create_task(_periodic_fetch())


@app.on_event("shutdown")
async def stop_periodic_fetch():
    task = getattr(app.state, "fetch_task", None)
    if task:
        task.cancel()
        with suppress(Exception):
            await task
    from redis_client import close_redis_connections

    await close_redis_connections()
