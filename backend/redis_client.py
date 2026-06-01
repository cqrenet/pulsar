"""Async Redis client singleton for caching and job queue."""

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from config import REDIS_URL

_arq_pool: ArqRedis | None = None
_plain_redis: aioredis.Redis | None = None


async def get_arq_pool() -> ArqRedis:
    """Return a shared arq pool (ArqRedis extends redis.asyncio.Redis)."""
    global _arq_pool
    if _arq_pool is None:
        _arq_pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    return _arq_pool


async def get_redis() -> aioredis.Redis:
    """Return a shared plain async Redis client."""
    global _plain_redis
    if _plain_redis is None:
        _plain_redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _plain_redis


async def close_redis_connections():
    """Close all Redis connections (call on shutdown)."""
    global _arq_pool, _plain_redis
    if _arq_pool:
        await _arq_pool.close()
        _arq_pool = None
    if _plain_redis:
        await _plain_redis.close()
        _plain_redis = None
