"""Rejects access tokens auth-svc has revoked before they expire
(logout, password reset, refresh-token reuse). See auth-svc's
src/core/revocation.py for the key layout.

A Redis outage is logged and the request proceeds on the token's own
expiry (at most the access-token lifetime) — failing closed would turn
a cache outage into a full login outage for every user.
"""
import logging

from src.core import redis_client

logger = logging.getLogger("api-gateway-svc.revocation")


async def is_revoked(claims: dict) -> bool:
    client = redis_client.get_redis()
    if client is None:
        return False
    try:
        jti = claims.get("jti")
        if jti and await client.exists(f"revoked:jti:{jti}"):
            return True
        cutoff = await client.get(f"revoked:cred:{claims['tenant_id']}:{claims['sub']}")
        return cutoff is not None and int(claims.get("iat", 0)) < int(cutoff)
    except Exception:  # noqa: BLE001
        logger.warning("revocation check unavailable; relying on token expiry", exc_info=True)
        return False
