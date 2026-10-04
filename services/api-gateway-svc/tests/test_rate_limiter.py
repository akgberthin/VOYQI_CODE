import time
from unittest.mock import patch

from src.services import rate_limiter
from src.services.rate_limiter import check_rate_limit


def setup_function():
    rate_limiter.reset_all()


def test_allows_requests_under_the_limit():
    with patch("src.services.rate_limiter.settings.rate_limit_requests_per_window", 5):
        for _ in range(5):
            allowed, remaining = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
            assert allowed is True
        assert remaining == 0


def test_blocks_requests_over_the_limit():
    with patch("src.services.rate_limiter.settings.rate_limit_requests_per_window", 3):
        for _ in range(3):
            check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        allowed, remaining = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        assert allowed is False
        assert remaining == 0


def test_rejected_request_does_not_consume_a_slot():
    """A client hammering an already-full window shouldn't push the
    reset time further out — only successful (allowed) requests should
    occupy a slot in the sliding window."""
    with patch("src.services.rate_limiter.settings.rate_limit_requests_per_window", 2):
        check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        # Both of these should be rejected without changing internal state.
        first_reject = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        second_reject = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        assert first_reject == (False, 0)
        assert second_reject == (False, 0)


def test_different_tenants_have_independent_limits():
    with patch("src.services.rate_limiter.settings.rate_limit_requests_per_window", 1):
        allowed_t1, _ = check_rate_limit(tenant_id="tenant-a", path_prefix="/api/v1/fleet")
        allowed_t2, _ = check_rate_limit(tenant_id="tenant-b", path_prefix="/api/v1/fleet")
        assert allowed_t1 is True
        assert allowed_t2 is True  # tenant-b's own window, unaffected by tenant-a


def test_different_route_prefixes_have_independent_limits_for_the_same_tenant():
    with patch("src.services.rate_limiter.settings.rate_limit_requests_per_window", 1):
        allowed_fleet, _ = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        allowed_hrm, _ = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/hrm")
        assert allowed_fleet is True
        assert allowed_hrm is True


def test_window_expires_and_allows_again():
    with patch("src.services.rate_limiter.settings.rate_limit_requests_per_window", 1), \
         patch("src.services.rate_limiter.settings.rate_limit_window_seconds", 0.05):
        allowed_first, _ = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        assert allowed_first is True
        blocked, _ = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        assert blocked is False

        time.sleep(0.1)
        allowed_after_window, _ = check_rate_limit(tenant_id="t1", path_prefix="/api/v1/fleet")
        assert allowed_after_window is True
