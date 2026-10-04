"""In-memory sliding-window rate limiter, keyed by (tenant_id, path prefix).

Honest limitation: this state lives in one process's memory. A single
gateway replica enforces it correctly; multiple replicas behind a load
balancer would each track their own counters, so the *effective* limit
becomes (configured limit x replica count) rather than the configured
limit. That's a real gap for a multi-replica production deployment — the
fix is a shared store (Redis INCR + EXPIRE is the standard pattern), not
a bigger in-memory structure. Documented here rather than silently wrong
once this ships past a single instance.
"""
import time
from collections import defaultdict, deque

from src.core.config import get_settings

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


def reset_all() -> None:
    """Test-only escape hatch — production code never needs this."""
    _windows.clear()
