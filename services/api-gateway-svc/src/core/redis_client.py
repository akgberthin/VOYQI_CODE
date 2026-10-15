"""Lazily-created shared Redis client. None when GATEWAY_REDIS_URL is
unset (tests, minimal local runs) — callers fall back accordingly."""
from src.core.config import get_settings

_client = None


def get_redis():
    global _client
    url = get_settings().redis_url
    if not url:
        return None
    if _client is None:
        import redis.asyncio as redis

        _client = redis.from_url(url, socket_timeout=2.0)
    return _client
