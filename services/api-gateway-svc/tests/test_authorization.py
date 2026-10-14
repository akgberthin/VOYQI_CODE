from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services import authorization
from src.services.authorization import (
    AuthorizationError,
    authorize,
    is_allowed,
    required_permission,
)
from src.services.routing import resolve_route

USER = "11111111-1111-1111-1111-111111111111"
CLAIMS = {"tenant_id": "voyqi-holdings", "user_id": USER, "sub": "cred-1"}
# Captured at import, before conftest's grant-all fixture patches it.
REAL_PERMISSIONS_FOR = authorization.permissions_for


@pytest.fixture
def grant(monkeypatch):
    """Overrides conftest's grant-all: the caller holds exactly these."""
    def _grant(*permissions):
        async def _perms(tenant_id, user_id):
            return frozenset(permissions)
        monkeypatch.setattr(authorization, "permissions_for", _perms)
    return _grant


def test_permission_is_module_plus_read_or_write():
    fleet = resolve_route("/api/v1/fleet/vehicles")
    assert required_permission(fleet, "GET") == "fleet.read"
    assert required_permission(fleet, "POST") == "fleet.write"
    assert required_permission(resolve_route("/api/v1/insurance-risk/policies"), "DELETE") == "insurance-risk.write"


@pytest.mark.parametrize("held,required,allowed", [
    ({"fleet.read"}, "fleet.read", True),
    ({"fleet.read"}, "fleet.write", False),
    ({"fleet.*"}, "fleet.write", True),
    ({"*"}, "originations.write", True),
    ({"rental.*"}, "fleet.read", False),
    (set(), "fleet.read", False),
])
def test_wildcards(held, required, allowed):
    assert is_allowed(frozenset(held), required) is allowed


async def test_member_cannot_reach_admin_apis(grant):
    grant("subscription.*", "rental.*")
    route = resolve_route("/api/v1/rbac/roles")
    with pytest.raises(AuthorizationError) as exc:
        await authorize(CLAIMS, route, "POST", "/api/v1/rbac/roles")
    assert exc.value.status_code == 403
    assert "rbac.write" in exc.value.detail


async def test_read_only_role_cannot_write(grant):
    grant("originations.read")
    route = resolve_route("/api/v1/originations/credit-decisions")
    await authorize(CLAIMS, route, "GET", "/api/v1/originations/credit-decisions")
    with pytest.raises(AuthorizationError):
        await authorize(CLAIMS, route, "POST", "/api/v1/originations/credit-decisions/x/decide")


async def test_self_service_needs_no_role(grant):
    grant()  # no permissions at all
    await authorize(CLAIMS, resolve_route("/api/v1/auth/me"), "GET", "/api/v1/auth/me")
    await authorize(CLAIMS, resolve_route(f"/api/v1/user/users/{USER}"), "PATCH", f"/api/v1/user/users/{USER}")
    with pytest.raises(AuthorizationError):
        other = "/api/v1/user/users/22222222-2222-2222-2222-222222222222"
        await authorize(CLAIMS, resolve_route(other), "GET", other)


async def test_enforcement_can_be_switched_off(grant, monkeypatch):
    from src.core.config import get_settings

    grant()
    monkeypatch.setattr(get_settings(), "enforce_rbac", False)
    await authorize(CLAIMS, resolve_route("/api/v1/rbac/roles"), "POST", "/api/v1/rbac/roles")


def _rbac_client(response=None, side_effect=None):
    client = AsyncMock()
    client.get = AsyncMock(return_value=response, side_effect=side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def test_fetch_permissions_reads_rbac_effective_permissions():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"effective_permissions": ["fleet.read", "rental.*"], "denied": ["fleet.write"]}
    client = _rbac_client(resp)
    with patch("src.services.authorization.httpx.AsyncClient", return_value=client):
        perms = await authorization.fetch_permissions("voyqi-holdings", USER)
    assert perms == frozenset({"fleet.read", "rental.*"})
    url = client.get.call_args.args[0]
    assert url.endswith(f"/api/v1/rbac/users/{USER}/effective-permissions")
    assert client.get.call_args.kwargs["headers"] == {"x-tenant-id": "voyqi-holdings"}


async def test_unknown_rbac_user_has_no_permissions():
    with patch("src.services.authorization.httpx.AsyncClient", return_value=_rbac_client(MagicMock(status_code=404))):
        assert await authorization.fetch_permissions("t", USER) == frozenset()


async def test_rbac_unreachable_fails_closed():
    client = _rbac_client(side_effect=httpx.ConnectError("down"))
    with patch("src.services.authorization.httpx.AsyncClient", return_value=client):
        with pytest.raises(AuthorizationError) as exc:
            await authorization.fetch_permissions("t", USER)
    assert exc.value.status_code == 503


async def test_permissions_are_cached_per_user(monkeypatch):
    calls = []

    async def _fetch(tenant_id, user_id):
        calls.append(user_id)
        return frozenset({"fleet.read"})

    monkeypatch.setattr(authorization, "fetch_permissions", _fetch)
    authorization.reset_cache()

    await REAL_PERMISSIONS_FOR("t", "u-1")
    await REAL_PERMISSIONS_FOR("t", "u-1")
    await REAL_PERMISSIONS_FOR("t", "u-2")
    assert calls == ["u-1", "u-2"]
