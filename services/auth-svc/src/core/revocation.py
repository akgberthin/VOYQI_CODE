"""Access-token revocation in Redis, shared with api-gateway-svc.

Access tokens are stateless JWTs the gateway verifies without calling
this service, so ending a session early needs a shared record:

  revoked:jti:<jti>                 one token (logout)
  revoked:cred:<tenant>:<cred id>   unix time; every token issued before
                                    it is dead (password reset, refresh
                                    token reuse — suspected theft)

Both expire with the longest-lived access token, after which nothing
they cover can still be valid. Without AUTH_REDIS_URL (tests, minimal
local runs) revocation is a no-op; a Redis error is logged, never
raised, so it cannot block a logout or password reset.
"""
import logging
import time

from src.core.config import get_settings

logger = logging.getLogger("auth-svc.revocation")
_client = None


def _redis():
    global _client
    url = get_settings().redis_url
    if not url:
        return None
    if _client is None:
        import redis.asyncio as redis

        _client = redis.from_url(url, socket_timeout=2.0)
    return _client


def _ttl_seconds() -> int:
    return get_settings().access_token_ttl_minutes * 60 + 60


async def revoke_token(jti: str | None, exp: int | None) -> None:
    client = _redis()
    if client is None or not jti:
        return
    ttl = max(int((exp or 0) - time.time()), 1) if exp else _ttl_seconds()
    try:
        await client.setex(f"revoked:jti:{jti}", ttl, 1)
    except Exception:  # noqa: BLE001
        logger.warning("could not record token revocation for jti=%s", jti, exc_info=True)


async def revoke_all_for_credential(tenant_id: str, user_credential_id) -> None:
    client = _redis()
    if client is None or user_credential_id is None:
        return
    try:
        await client.setex(f"revoked:cred:{tenant_id}:{user_credential_id}", _ttl_seconds(), int(time.time()))
    except Exception:  # noqa: BLE001
        logger.warning("could not revoke sessions for credential %s", user_credential_id, exc_info=True)
