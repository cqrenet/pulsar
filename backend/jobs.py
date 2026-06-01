"""arq worker configuration. Currently no background jobs are defined.
Redis is still used directly by the rate limiter and redis_client helpers."""

import structlog
from arq.connections import RedisSettings
from config import REDIS_URL

logger = structlog.get_logger("pulsar.jobs")


async def startup(ctx):
    from redis.asyncio import Redis

    ctx["redis"] = Redis.from_url(REDIS_URL, decode_responses=True)


async def shutdown(ctx):
    await ctx["redis"].close()


class WorkerSettings:
    functions = []
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    on_startup = startup
    on_shutdown = shutdown
