"""Async Redis client singleton — used by RecentCustomerBuffer + others.

Mirror of Spring Data Redis's StringRedisTemplate. FastAPI dependency
injection hands out the singleton to consumers via `Depends(get_redis)`.
"""

from __future__ import annotations

from redis.asyncio import Redis

from iris_service.config.settings import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Lazy singleton — created on first call. Async client = no blocking calls."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db,
            decode_responses=True,  # bytes → str transparently
        )
    return _client


async def close_redis() -> None:
    """Called by app lifespan on shutdown — releases connection pool."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
