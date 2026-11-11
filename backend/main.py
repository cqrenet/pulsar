import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import structlog
from config import (
    AUTH_ADMIN_ROLES,
    AUTH_ALLOWED_GROUPS,
    AUTH_ALLOWED_ROLES,
    AUTH_ENABLED,
    DOCS_ENABLED,
    ENABLE_PERIODIC_FETCH,
    FETCH_INTERVAL_MINUTES,
    MCP_API_KEY,
    WEBHOOK_CLIENT_SECRET,
    missing_auth_settings,
)
from database import setup_indexes
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from ingest import run_fetch
from middleware import register_middleware
from redis_client import close_redis_connections
from routes.alerts import router as alerts_router
from routes.config import router as config_router
from routes.events import router as events_router
from routes.fetch import router as fetch_router
from routes.health import router as health_router
from routes.mcp import mcp_app
from routes.rules import router as rules_router
from routes.saved_searches import router as saved_searches_router
from routes.summary import router as summary_router
from routes.system import OAUTH_CLIENT_ID, OAUTH_DISCOVERY_ENABLED, OAUTH_TENANT_ID, app_version
from routes.system import router as system_router
from routes.webhooks import router as webhooks_router
from rules import seed_default_rules


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


def _warn_insecure_config() -> None:
    if not AUTH_ENABLED and not MCP_API_KEY:
        logger.warning(
            "MCP endpoint is open: AUTH_ENABLED=false and MCP_API_KEY is not set. "
            "Anyone who can reach /mcp can query audit events. "
            "Set MCP_API_KEY for a simple API key gate, or AUTH_ENABLED=true for Entra ID auth."
        )
    if AUTH_ENABLED and not AUTH_ALLOWED_ROLES and not AUTH_ALLOWED_GROUPS:
        logger.warning(
            "AUTH_ENABLED is true but no AUTH_ALLOWED_ROLES or AUTH_ALLOWED_GROUPS are configured. "
            "Any Entra user in the tenant can authenticate and access PULSAR. "
            "Set AUTH_ALLOWED_ROLES or AUTH_ALLOWED_GROUPS to restrict access."
        )
    if AUTH_ENABLED and not AUTH_ADMIN_ROLES:
        logger.warning(
            "AUTH_ADMIN_ROLES is not set. Every signed-in user can change or delete alert rules. "
            "Set AUTH_ADMIN_ROLES to the app role(s) allowed to manage PULSAR."
        )
    if not WEBHOOK_CLIENT_SECRET:
        logger.warning(
            "WEBHOOK_CLIENT_SECRET is not set. Graph webhook notifications will be rejected. "
            "Set WEBHOOK_CLIENT_SECRET to the clientState used when creating Graph subscriptions."
        )


async def _periodic_fetch():
    while True:
        try:
            await asyncio.to_thread(run_fetch)
            logger.info("Periodic fetch completed.")
        except Exception as exc:
            logger.error("Periodic fetch failed", error=str(exc))
        await asyncio.sleep(FETCH_INTERVAL_MINUTES * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = missing_auth_settings()
    if missing:
        # Fail closed: a half-configured auth setup must not come up and serve data.
        raise RuntimeError(f"AUTH_ENABLED=true but {', '.join(missing)} not set. Refusing to start.")
    setup_indexes()
    seed_default_rules()
    logger.info(
        "PULSAR startup",
        version=app_version(),
        auth_enabled=AUTH_ENABLED,
        mcp_api_key_set=bool(MCP_API_KEY),
        oauth_discovery=OAUTH_DISCOVERY_ENABLED,
    )
    if OAUTH_DISCOVERY_ENABLED:
        logger.info(
            "OAuth discovery endpoint active",
            path="/.well-known/oauth-authorization-server",
            tenant=OAUTH_TENANT_ID,
            client=OAUTH_CLIENT_ID,
        )
    _warn_insecure_config()
    fetch_task = asyncio.create_task(_periodic_fetch()) if ENABLE_PERIODIC_FETCH else None
    try:
        yield
    finally:
        if fetch_task:
            fetch_task.cancel()
            with suppress(BaseException):
                await fetch_task
        await close_redis_connections()


# OpenAPI docs are disabled in production by default.
app = FastAPI(
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
    lifespan=lifespan,
)
register_middleware(app)

for router in (
    fetch_router,
    events_router,
    summary_router,
    config_router,
    webhooks_router,
    health_router,
    saved_searches_router,
    rules_router,
    alerts_router,
):
    app.include_router(router, prefix="/api")
app.include_router(system_router)
app.mount("/mcp", mcp_app)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Return generic error messages for unhandled exceptions to avoid info leakage."""
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.detail}, headers=getattr(exc, "headers", None) or {}
        )
    logger.error("Unhandled exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.mount("/", StaticFiles(directory=Path(__file__).parent / "frontend", html=True), name="frontend")
