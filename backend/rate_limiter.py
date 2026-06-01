"""Simple Redis-backed fixed-window rate limiter."""

import time

import structlog
from config import RATE_LIMIT_ENABLED, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS
from fastapi import HTTPException, Request
from redis_client import get_redis

logger = structlog.get_logger("pulsar.rate_limit")


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail="Rate limit exceeded. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )


def _get_identifier(request: Request) -> str:
    """Best-effort client identifier: authenticated sub, or X-Forwarded-For, or client host."""
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict):
        sub = user.get("sub")
        if sub and sub != "anonymous":
            return f"user:{sub}"

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"

    return f"ip:{request.client.host if request.client else 'unknown'}"


def _get_path_category(path: str) -> str:
    """Bucket paths into rate-limit categories."""
    if path.startswith("/api/fetch"):
        return "fetch"
    if path.startswith("/api/events/bulk-tags"):
        return "write"
    return "default"


def _limit_for_category(category: str) -> tuple[int, int]:
    """Return (max_requests, window_seconds) for a category."""
    if category == "fetch":
        return (10, 3600)  # 10 per hour
    if category == "write":
        return (20, 60)  # 20 per minute
    return (RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


async def check_rate_limit(request: Request):
    """Raise RateLimitExceeded if the client has exceeded their quota."""
    if not RATE_LIMIT_ENABLED:
        return

    category = _get_path_category(request.url.path)
    limit, window = _limit_for_category(category)

    identifier = _get_identifier(request)
    now = int(time.time())
    window_key = now // window
    redis_key = f"rate_limit:{identifier}:{category}:{window_key}"

    try:
        redis = await get_redis()
        count = await redis.incr(redis_key)
        if count == 1:
            await redis.expire(redis_key, window)
        if count > limit:
            raise RateLimitExceeded(retry_after=window - (now % window))
    except RateLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("Rate limiter Redis error; failing closed", error=str(exc))
        raise RateLimitExceeded(retry_after=60) from None
