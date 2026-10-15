"""Shared rate limiting and access-token revocation via Redis."""
import pytest

from src.core import redis_client
from src.services import rate_limiter
from src.services.revocation import is_revoked


class FakeRedis:
    def __init__(self):
        self.data: dict[str, object] = {}

    async def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def decr(self, key):
        self.data[key] = int(self.data[key]) - 1
        return self.data[key]

    async def expire(self, key, ttl):
        return True

    async def exists(self, key):
        return int(key in self.data)

    async def get(self, key):
        return self.data.get(key)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    return fake


async def test_shared_limit_is_enforced_from_redis_and_rejections_dont_consume(redis, monkeypatch):
    monkeypatch.setattr(rate_limiter.settings, "rate_limit_requests_per_window", 2)
    results = [await rate_limiter.check_rate_limit_shared(tenant_id="t", path_prefix="/api/v1/fleet") for _ in range(4)]
    assert [allowed for allowed, _ in results] == [True, True, False, False]
    [count] = [v for k, v in redis.data.items() if k.startswith("ratelimit:")]
    assert count == 2


async def test_falls_back_to_per_process_when_redis_fails(monkeypatch):
    class Broken:
        async def incr(self, key):
            raise ConnectionError("down")

    monkeypatch.setattr(redis_client, "get_redis", lambda: Broken())
    rate_limiter.reset_all()
    allowed, _ = await rate_limiter.check_rate_limit_shared(tenant_id="t", path_prefix="/api/v1/fleet")
    assert allowed is True


CLAIMS = {"sub": "cred-1", "tenant_id": "t", "jti": "j-1", "iat": 1_000}


async def test_logged_out_token_is_revoked(redis):
    assert await is_revoked(CLAIMS) is False
    redis.data["revoked:jti:j-1"] = 1
    assert await is_revoked(CLAIMS) is True


async def test_tokens_issued_before_a_credential_cutoff_are_revoked(redis):
    redis.data["revoked:cred:t:cred-1"] = b"1500"
    assert await is_revoked(CLAIMS) is True                      # issued at 1000
    assert await is_revoked({**CLAIMS, "jti": "j-2", "iat": 1_600}) is False  # re-login after the reset


async def test_no_redis_means_no_revocation_check(monkeypatch):
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    assert await is_revoked(CLAIMS) is False
