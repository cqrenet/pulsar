import structlog
from config import settings
from fastapi import APIRouter

router = APIRouter()
logger = structlog.get_logger("pulsar.config")


@router.get("/config/auth")
def auth_config():
    logger.debug("Auth config requested", auth_enabled=settings.AUTH_ENABLED)
    return {
        "auth_enabled": settings.AUTH_ENABLED,
        "tenant_id": settings.AUTH_TENANT_ID if settings.AUTH_ENABLED else "",
        "client_id": settings.AUTH_CLIENT_ID if settings.AUTH_ENABLED else "",
        "scope": settings.AUTH_SCOPE,
        "redirect_uri": None,  # frontend uses window.location.origin by default
    }


@router.get("/config/features")
def features_config():
    return {
        "default_page_size": settings.DEFAULT_PAGE_SIZE,
    }
