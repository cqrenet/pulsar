import asyncio
import contextvars
import threading
import time

import requests
import structlog
from config import (
    AUTH_ALLOWED_GROUPS,
    AUTH_ALLOWED_ROLES,
    AUTH_CLIENT_ID,
    AUTH_ENABLED,
    AUTH_TENANT_ID,
    PRIVACY_SERVICE_ROLES,
    PRIVACY_SERVICES,
)
from fastapi import Header, HTTPException
from jwt import ExpiredSignatureError, InvalidTokenError, decode
from jwt.algorithms import RSAAlgorithm

# Thread-/task-local storage for verified auth claims (used by audit middleware)
_auth_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar("auth_context", default=None)

JWKS_CACHE = {"exp": 0, "keys": []}
_jwks_lock = threading.Lock()
logger = structlog.get_logger("pulsar.auth")


def _fetch_jwks_blocking() -> list:
    """Fetch JWKS from Microsoft — runs in a thread, never in the event loop."""
    oidc = requests.get(
        f"https://login.microsoftonline.com/{AUTH_TENANT_ID}/v2.0/.well-known/openid-configuration",
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


def _allowed(claims: dict, allowed_roles: set[str], allowed_groups: set[str]) -> bool:
    if not allowed_roles and not allowed_groups:
        return True
    roles = set(claims.get("roles", []) or claims.get("role", []) or [])
    groups = set(claims.get("groups", []) or [])
    return bool(
        (allowed_roles and roles.intersection(allowed_roles))
        or (allowed_groups and groups.intersection(allowed_groups))
    )


def _decode_token(token: str, jwks):
    try:
        import json

        from jwt import get_unverified_header

        header = get_unverified_header(token)
        kid = header.get("kid")
        key_dict = next((k for k in jwks if k.get("kid") == kid), None)
        if not key_dict:
            raise HTTPException(status_code=401, detail="Invalid token: signing key not found")

        pub_key = RSAAlgorithm.from_jwk(json.dumps(key_dict))
        decode_kwargs = {"algorithms": ["RS256"]}
        if AUTH_CLIENT_ID:
            # Entra v2.0 tokens issued for a custom scope (e.g. api://<id>/user_impersonation)
            # carry aud = "api://<clientId>", not the bare GUID.  Accept both so operators
            # can set AUTH_CLIENT_ID to either the GUID or the full Application ID URI.
            bare = AUTH_CLIENT_ID.removeprefix("api://")
            decode_kwargs["audience"] = [bare, f"api://{bare}"]
        claims = decode(token, pub_key, **decode_kwargs)

        tid = claims.get("tid")
        iss = claims.get("iss", "")
        if AUTH_TENANT_ID and tid and tid != AUTH_TENANT_ID:
            raise HTTPException(status_code=401, detail="Invalid tenant")
        if AUTH_TENANT_ID and AUTH_TENANT_ID not in iss:
            raise HTTPException(status_code=401, detail="Invalid issuer")
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


def user_can_access_privacy_services(claims: dict) -> bool:
    """Check if the user has roles that grant access to privacy-sensitive services."""
    if not PRIVACY_SERVICES or not PRIVACY_SERVICE_ROLES:
        return True
    user_roles = set(claims.get("roles", []) or claims.get("role", []) or [])
    return bool(user_roles.intersection(PRIVACY_SERVICE_ROLES))


async def require_auth(authorization: str | None = Header(None)):
    if not AUTH_ENABLED:
        user = {"sub": "anonymous"}
        _auth_context.set(user)
        return user

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1]
    jwks = await _get_jwks_async()
    claims = _decode_token(token, jwks)

    if not _allowed(claims, AUTH_ALLOWED_ROLES, AUTH_ALLOWED_GROUPS):
        raise HTTPException(status_code=403, detail="Forbidden")

    _auth_context.set(claims)
    return claims
