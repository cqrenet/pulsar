import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject or propagate a correlation ID for every request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cid = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(correlation_id=cid)
        response = await call_next(request)
        response.headers["x-request-id"] = cid
        return response
