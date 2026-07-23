"""
Redis cache layer.

Strategy:
- Cache-aside pattern: read from cache first, populate on miss
- Explicit TTLs per entity type
- JSON serialization for complex objects
- Graceful degradation: on Redis failure, fall through to API
"""

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


class CacheKeys:
    @staticmethod
    def user_playlists(user_spotify_id: str) -> str:
        return f"playlists:{user_spotify_id}"

    @staticmethod
    def audio_features(track_spotify_id: str) -> str:
        return f"audio_features:{track_spotify_id}"

    @staticmethod
    def playlist_snapshot(playlist_spotify_id: str) -> str:
        return f"snapshot:{playlist_spotify_id}"


async def cache_get(key: str) -> Any | None:
    """Retrieve a cached value. Returns None on miss or error."""
    try:
        redis = await get_redis()
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("cache_get_failed", key=key, error=str(exc))
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    """Store a value with TTL seconds. Fails silently to avoid blocking ingestion."""
    try:
        redis = await get_redis()
        await redis.setex(key, ttl, json.dumps(value))
    except Exception as exc:
        logger.warning("cache_set_failed", key=key, error=str(exc))


async def cache_delete(key: str) -> None:
    """Invalidate a cache entry."""
    try:
        redis = await get_redis()
        await redis.delete(key)
    except Exception as exc:
        logger.warning("cache_delete_failed", key=key, error=str(exc))


async def get_cached_playlists(user_spotify_id: str) -> list[dict] | None:
    return await cache_get(CacheKeys.user_playlists(user_spotify_id))


async def set_cached_playlists(user_spotify_id: str, playlists: list[dict]) -> None:
    await cache_set(
        CacheKeys.user_playlists(user_spotify_id),
        playlists,
        ttl=settings.cache_ttl_playlists,
    )


async def get_cached_audio_features(track_spotify_id: str) -> dict | None:
    return await cache_get(CacheKeys.audio_features(track_spotify_id))


async def set_cached_audio_features(track_spotify_id: str, features: dict) -> None:
    await cache_set(
        CacheKeys.audio_features(track_spotify_id),
        features,
        ttl=settings.cache_ttl_audio_features,
    )
