"""Rate limiter keyed by (tenant_id, path prefix).

With GATEWAY_REDIS_URL set, counters live in Redis (fixed window,
INCR + EXPIRE), so every gateway replica enforces one shared limit.
Without it — or if Redis is unreachable — each process falls back to its
own in-memory sliding window, which is exact for a single replica but
multiplies the effective limit by the replica count.
"""
import logging
import time
from collections import defaultdict, deque

from src.core import redis_client
from src.core.config import get_settings

logger = logging.getLogger("api-gateway-svc.rate_limiter")

settings = get_settings()

_windows: dict[str, deque[float]] = defaultdict(deque)


def _key(tenant_id: str, path_prefix: str) -> str:
    return f"{tenant_id}:{path_prefix}"


def check_rate_limit(*, tenant_id: str, path_prefix: str) -> tuple[bool, int]:
    """Returns (allowed, remaining). Records the attempt only if allowed —
    a rejected request doesn't consume a slot, so a client isn't
    permanently locked out by rapid retries against an already-full window."""
    now = time.monotonic()
    window_start = now - settings.rate_limit_window_seconds
    key = _key(tenant_id, path_prefix)
    timestamps = _windows[key]

    while timestamps and timestamps[0] < window_start:
        timestamps.popleft()

    if len(timestamps) >= settings.rate_limit_requests_per_window:
        return False, 0

    timestamps.append(now)
    remaining = settings.rate_limit_requests_per_window - len(timestamps)
    return True, remaining


async def check_rate_limit_shared(*, tenant_id: str, path_prefix: str) -> tuple[bool, int]:
    client = redis_client.get_redis()
    if client is None:
        return check_rate_limit(tenant_id=tenant_id, path_prefix=path_prefix)
    window = settings.rate_limit_window_seconds
    key = f"ratelimit:{_key(tenant_id, path_prefix)}:{int(time.time()) // window}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window)
        if count > settings.rate_limit_requests_per_window:
            await client.decr(key)  # a rejected request doesn't consume a slot
            return False, 0
        return True, settings.rate_limit_requests_per_window - count
    except Exception:  # noqa: BLE001
        logger.warning("shared rate limit unavailable; falling back to per-process", exc_info=True)
        return check_rate_limit(tenant_id=tenant_id, path_prefix=path_prefix)


def reset_all() -> None:
    """Test-only escape hatch — production code never needs this."""
    _windows.clear()
