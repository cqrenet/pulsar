"""Simple Redis-backed fixed-window rate limiter."""

import time

import structlog
from config import RATE_LIMIT_ENABLED, RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS
from fastapi import HTTPException, Request
from redis_client import get_redis
from utils.net import client_ip

logger = structlog.get_logger("pulsar.rate_limit")


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail="Rate limit exceeded. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )


def _get_identifier(request: Request) -> str:
    """Client identifier. Runs before auth, so it is the client IP (trusted-proxy aware, not spoofable)."""
    return f"ip:{client_ip(request) or 'unknown'}"


# Path prefix -> category, first match wins. Everything else is "default".
_PATH_CATEGORIES = (("/api/fetch", "fetch"), ("/api/events/bulk-tags", "write"))

# Category -> (max requests, window seconds). "default" comes from configuration.
_CATEGORY_LIMITS = {
    "fetch": (10, 3600),  # 10 per hour
    "write": (20, 60),  # 20 per minute
    "default": (RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS),
}


def _get_path_category(path: str) -> str:
    """Bucket paths into rate-limit categories."""
    return next((cat for prefix, cat in _PATH_CATEGORIES if path.startswith(prefix)), "default")


def _limit_for_category(category: str) -> tuple[int, int]:
    """Return (max_requests, window_seconds) for a category."""
    return _CATEGORY_LIMITS[category]


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
