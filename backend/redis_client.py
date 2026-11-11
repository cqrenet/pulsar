"""Async Redis client singleton (rate limiting)."""

import redis.asyncio as aioredis
from config import REDIS_URL

_plain_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return a shared plain async Redis client."""
    global _plain_redis
    if _plain_redis is None:
        _plain_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _plain_redis


async def close_redis_connections():
    """Close all Redis connections (call on shutdown)."""
    global _plain_redis
    if _plain_redis:
        await _plain_redis.close()
        _plain_redis = None
