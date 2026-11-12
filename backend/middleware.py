"""HTTP middleware: correlation IDs, metrics, security headers, rate limiting and request auditing."""

import re
import time
import uuid

import structlog
from audit_trail import log_action
from config import settings
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from metrics import observe_request
from rate_limiter import check_rate_limit
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from utils.net import client_ip  # noqa: F401  (re-exported: `from middleware import client_ip`)

logger = structlog.get_logger("pulsar.middleware")

RATE_LIMIT_EXEMPT_PATHS = {"/api/config/auth", "/api/config/features", "/health", "/metrics"}
MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")

# Exact library paths, not whole CDNs: any package on jsdelivr would otherwise be a script gadget.
# No 'unsafe-eval': Alpine's CSP build evaluates templates without eval/new Function.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.15.12/ https://alcdn.msauth.net/browser/2.37.0/; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' https://login.microsoftonline.com; "
    "frame-src 'self' https://login.microsoftonline.com; "
    "form-action 'self' https://login.microsoftonline.com; "
    "img-src 'self' data:; "
    "font-src 'self' data:;"
)
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject or propagate a correlation ID for every request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("x-request-id", "")
        # Client-supplied IDs end up in audit records and response headers: accept only a safe shape.
        cid = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(correlation_id=cid)
        response = await call_next(request)
        response.headers["x-request-id"] = cid
        return response


async def prometheus_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    path = getattr(request.scope.get("route"), "path", request.url.path)
    observe_request(request.method, path, response.status_code, time.time() - start)
    return response


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/") or path in ("/", "/index.html"):
        # No caching of HTML and API responses, and a CSP that allows the MSAL auth flow.
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["Strict-Transport-Security"] = "max-age=31536000"  # ignored by browsers over plain HTTP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = PERMISSIONS_POLICY
    return response


async def rate_limit_middleware(request: Request, call_next):
    """Apply Redis-backed rate limiting before processing the request."""
    path = request.url.path
    if path.startswith(("/api/", "/mcp")) and path not in RATE_LIMIT_EXEMPT_PATHS:
        await check_rate_limit(request)
    return await call_next(request)


async def audit_middleware(request: Request, call_next):
    """One audit record per mutating request: who, which action, outcome. Handlers add detail via set_audit()."""
    response = await call_next(request)
    if request.url.path.startswith("/api/") and request.method in MUTATING_METHODS:
        claims = getattr(request.state, "user", None)  # set by require_auth
        user = claims.get("sub", "unknown") if isinstance(claims, dict) else "anonymous"
        audit = getattr(request.state, "audit", None) or {}
        log_action(
            action=audit.get("action") or request.method.lower(),
            resource=request.url.path,
            details={**audit.get("details", {}), "method": request.method, "status_code": response.status_code},
            user=user,
        )
    return response


def register_middleware(app: FastAPI) -> None:
    """Attach all middleware. Later registrations wrap earlier ones."""
    # When auth is enabled, never allow credentials with wildcard origins.
    allow_credentials = not (settings.AUTH_ENABLED and "*" in settings.CORS_ORIGINS)
    if not allow_credentials:
        logger.warning(
            "CORS wildcard (*) is insecure with AUTH_ENABLED=true and allow_credentials. "
            "Disabling credentials. Set CORS_ORIGINS to your actual origin(s)."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for fn in (prometheus_middleware, security_headers_middleware, rate_limit_middleware, audit_middleware):
        app.middleware("http")(fn)
    # Added last so it is outermost: the request ID is bound before anything else (rate limiting, audit) runs.
    app.add_middleware(CorrelationIdMiddleware)
