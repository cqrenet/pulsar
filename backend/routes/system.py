"""Unauthenticated operational endpoints: health, metrics, version, OAuth discovery."""

import os

import structlog
from config import settings
from database import db
from fastapi import APIRouter, HTTPException, Request, Response
from metrics import prometheus_metrics
from utils.net import client_ip, ip_in, parse_networks

router = APIRouter()
logger = structlog.get_logger("pulsar.system")

OAUTH_CLIENT_ID = settings.MCP_CLIENT_ID or settings.AUTH_CLIENT_ID
OAUTH_TENANT_ID = settings.AUTH_TENANT_ID
OAUTH_DISCOVERY_ENABLED = bool(OAUTH_TENANT_ID and OAUTH_CLIENT_ID)

_METRICS_NETWORKS = parse_networks(settings.METRICS_ALLOWED_IPS)


def app_version() -> str:
    return os.environ.get("VERSION", "unknown")


@router.get("/health")
async def health_check():
    try:
        db.command("ping")
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        logger.error("Health check failed", error=str(exc))
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@router.get("/metrics")
async def metrics(request: Request):
    # An empty allowlist means unrestricted.
    if _METRICS_NETWORKS and not ip_in(client_ip(request), _METRICS_NETWORKS):
        raise HTTPException(status_code=403, detail="Forbidden")
    return Response(content=prometheus_metrics(), media_type="text/plain")


@router.get("/api/version")
async def version():
    return {"version": app_version()}


# OAuth 2.0 authorization server metadata (RFC 8414). Lets Claude Desktop, AURORA and other MCP clients
# discover Entra auth. Served at the server root so it is found wherever /mcp is mounted.
if OAUTH_DISCOVERY_ENABLED:
    _base = f"https://login.microsoftonline.com/{OAUTH_TENANT_ID}"
    _metadata = {
        "issuer": f"{_base}/v2.0",
        "authorization_endpoint": f"{_base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{_base}/oauth2/v2.0/token",
        "scopes_supported": [f"api://{OAUTH_CLIENT_ID}/user_impersonation", "openid", "profile", "offline_access"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }

    @router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    async def oauth_authorization_server_metadata():
        return _metadata
