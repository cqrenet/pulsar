import structlog
from config import (
    AUTH_CLIENT_ID,
    AUTH_ENABLED,
    AUTH_SCOPE,
    AUTH_TENANT_ID,
    DEFAULT_PAGE_SIZE,
)
from fastapi import APIRouter

router = APIRouter()
logger = structlog.get_logger("pulsar.config")


@router.get("/config/auth")
def auth_config():
    logger.debug("Auth config requested", auth_enabled=AUTH_ENABLED)
    return {
        "auth_enabled": AUTH_ENABLED,
        "tenant_id": AUTH_TENANT_ID if AUTH_ENABLED else "",
        "client_id": AUTH_CLIENT_ID if AUTH_ENABLED else "",
        "scope": AUTH_SCOPE,
        "redirect_uri": None,  # frontend uses window.location.origin by default
    }


@router.get("/config/features")
def features_config():
    return {
        "default_page_size": DEFAULT_PAGE_SIZE,
    }
