import asyncio
import json
import threading
import time

import requests
import structlog
from config import settings
from fastapi import Depends, Header, HTTPException, Request
from jwt import ExpiredSignatureError, InvalidTokenError, decode, get_unverified_header
from jwt.algorithms import RSAAlgorithm

JWKS_CACHE = {"exp": 0, "keys": []}
_jwks_lock = threading.Lock()
logger = structlog.get_logger("pulsar.auth")


def _fetch_jwks_blocking() -> list:
    """Fetch JWKS from Microsoft — runs in a thread, never in the event loop."""
    oidc = requests.get(
        f"https://login.microsoftonline.com/{settings.AUTH_TENANT_ID}/v2.0/.well-known/openid-configuration",
        timeout=10,
    ).json()
    jwks_uri = oidc["jwks_uri"]
    return requests.get(jwks_uri, timeout=10).json()["keys"]


def _get_jwks():
    now = time.time()
    with _jwks_lock:
        if JWKS_CACHE["keys"] and JWKS_CACHE["exp"] > now:
            return JWKS_CACHE["keys"]
        keys = _fetch_jwks_blocking()
        JWKS_CACHE["keys"] = keys
        JWKS_CACHE["exp"] = now + 60 * 60  # cache 1h
        return keys


async def _get_jwks_async() -> list:
    """Non-blocking JWKS fetch: return from cache or refresh in a thread pool."""
    now = time.time()
    if JWKS_CACHE["keys"] and JWKS_CACHE["exp"] > now:
        return JWKS_CACHE["keys"]
    return await asyncio.to_thread(_get_jwks)


def _roles(claims: dict) -> set[str]:
    return set(claims.get("roles") or claims.get("role") or [])


def _allowed(claims: dict, allowed_roles: set[str], allowed_groups: set[str]) -> bool:
    if not allowed_roles and not allowed_groups:
        return True
    roles = _roles(claims)
    groups = set(claims.get("groups", []) or [])
    return bool(
        (allowed_roles and roles.intersection(allowed_roles))
        or (allowed_groups and groups.intersection(allowed_groups))
    )


def _decode_token(token: str, jwks):
    try:
        header = get_unverified_header(token)
        kid = header.get("kid")
        key_dict = next((k for k in jwks if k.get("kid") == kid), None)
        if not key_dict:
            raise HTTPException(status_code=401, detail="Invalid token: signing key not found")

        pub_key = RSAAlgorithm.from_jwk(json.dumps(key_dict))
        # A token without exp, iss or aud must never be valid (PyJWT only checks claims that are present).
        decode_kwargs = {"algorithms": ["RS256"], "options": {"require": ["exp", "iss", "aud"]}}
        if settings.AUTH_CLIENT_ID:
            # Entra v2.0 tokens issued for a custom scope (e.g. api://<id>/user_impersonation)
            # carry aud = "api://<clientId>", not the bare GUID.  Accept both so operators
            # can set AUTH_CLIENT_ID to either the GUID or the full Application ID URI.
            bare = settings.AUTH_CLIENT_ID.removeprefix("api://")
            decode_kwargs["audience"] = [bare, f"api://{bare}"]
        claims = decode(token, pub_key, **decode_kwargs)

        tid = claims.get("tid")
        iss = claims.get("iss", "")
        if settings.AUTH_TENANT_ID and tid != settings.AUTH_TENANT_ID:
            raise HTTPException(status_code=401, detail="Invalid tenant")
        if settings.AUTH_TENANT_ID:
            expected_issuer = f"https://login.microsoftonline.com/{settings.AUTH_TENANT_ID}/v2.0"
            if iss != expected_issuer:
                raise HTTPException(status_code=401, detail="Invalid issuer")
        # ID tokens share our audience and signing keys but are not API credentials.
        # Entra access tokens always name the calling client (v2 "azp", v1 "appid"); ID tokens never do.
        if not (claims.get("azp") or claims.get("appid")):
            raise HTTPException(status_code=401, detail="Invalid token: access token required")
        if settings.AUTH_SCOPE:
            expected_scope = settings.AUTH_SCOPE.rsplit("/", 1)[-1]
            token_scopes = set((claims.get("scp") or "").split())
            if expected_scope not in token_scopes:
                raise HTTPException(status_code=401, detail="Invalid token: required scope missing")
        return claims
    except HTTPException:
        raise
    except ExpiredSignatureError as exc:
        logger.warning("Token verification failed", error_type="ExpiredSignatureError", error=str(exc))
        raise HTTPException(status_code=401, detail="Token expired") from None
    except InvalidTokenError as exc:
        logger.warning("Token verification failed", error_type=type(exc).__name__, error=str(exc))
        raise HTTPException(status_code=401, detail=f"Invalid token ({type(exc).__name__})") from None
    except Exception as exc:
        logger.warning("Token verification failed", error_type=type(exc).__name__, error=str(exc))
        raise HTTPException(status_code=401, detail=f"Invalid token ({type(exc).__name__})") from None


def user_can_access_privacy_services(claims: dict | None) -> bool:
    """Check if the user has roles that grant access to privacy-sensitive services.

    Fails closed: when privacy services/operations are configured, only PRIVACY_SERVICE_ROLES see them.
    """
    if not settings.PRIVACY_SERVICES and not settings.PRIVACY_SENSITIVE_OPERATIONS:
        return True
    claims = claims or {}
    return bool(_roles(claims).intersection(settings.PRIVACY_SERVICE_ROLES))


def apply_privacy_filter(query: dict, claims: dict | None) -> dict:
    """Return `query` narrowed so the caller cannot see privacy-gated services or operations."""
    if user_can_access_privacy_services(claims):
        return query
    hidden = {}
    if settings.PRIVACY_SERVICES:
        # $exists: documents with no service at all (e.g. alerts from before the field existed) are hidden too.
        hidden["service"] = {"$exists": True, "$nin": sorted(settings.PRIVACY_SERVICES)}
    if settings.PRIVACY_SENSITIVE_OPERATIONS:
        hidden["operation"] = {"$nin": sorted(settings.PRIVACY_SENSITIVE_OPERATIONS)}
    return {"$and": [query, hidden]} if query else hidden


async def require_auth(authorization: str | None = Header(None), request: Request = None):
    user = await _authenticate(authorization)
    if request is not None:
        # Read by the audit middleware. A contextvar would not work: BaseHTTPMiddleware runs the
        # endpoint in a child task, so values set here never reach the middleware.
        request.state.user = user
    return user


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """Gate configuration changes (alert rules, manual fetch) behind AUTH_ADMIN_ROLES."""
    if settings.AUTH_ENABLED and not _allowed(user, settings.AUTH_ADMIN_ROLES, set()):
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


async def verify_bearer(token: str) -> dict:
    """Validate an Entra access token and the allowed role/group policy.

    Raises HTTPException: 401 for a bad token, 403 for a valid token without access.
    """
    claims = _decode_token(token, await _get_jwks_async())
    if not _allowed(claims, settings.AUTH_ALLOWED_ROLES, settings.AUTH_ALLOWED_GROUPS):
        raise HTTPException(status_code=403, detail="Forbidden")
    return claims


async def _authenticate(authorization: str | None) -> dict:
    if not settings.AUTH_ENABLED:
        return {"sub": "anonymous"}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return await verify_bearer(authorization.split(" ", 1)[1])
