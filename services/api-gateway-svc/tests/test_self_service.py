import json

import pytest

from src.services import authorization, self_service
from src.services.authorization import AuthorizationError, authorize
from src.services.routing import resolve_route

ME, OTHER = "user-me", "user-other"
CLAIMS = {"tenant_id": "t", "user_id": ME, "sub": "c"}
# record -> owner, as the owning services would report it
OWNERS = {
    ("member", "m-mine"): ME, ("member", "m-theirs"): OTHER,
    ("subscription", "s-mine"): "m-mine", ("subscription", "s-theirs"): "m-theirs",
    ("upgrade_case", "u-mine"): "m-mine",
    ("charge", "c-mine"): ME, ("charge", "c-theirs"): OTHER,
}


@pytest.fixture(autouse=True)
def member_role(monkeypatch):
    self_service.reset_cache()

    async def _perms(tenant_id, user_id):
        return frozenset({"subscription.self", "payment.self", "hub.read"})

    async def _owner(tenant_id, kind, record_id):
        return OWNERS.get((kind, record_id))

    monkeypatch.setattr(authorization, "permissions_for", _perms)
    monkeypatch.setattr(self_service, "fetch_owner", _owner)


async def _call(method, path, body=None):
    await authorize(CLAIMS, resolve_route(path), method, path, json.dumps(body).encode() if body is not None else b"")


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/v1/subscription/plans", None),
    ("GET", f"/api/v1/subscription/members/by-user-id/{ME}", None),
    ("GET", "/api/v1/subscription/members/m-mine", None),
    ("GET", "/api/v1/subscription/members/m-mine/invoices", None),
    ("POST", "/api/v1/subscription/subscriptions/s-mine/pause", {}),
    ("POST", "/api/v1/subscription/upgrade-cases/u-mine/accept", {}),
    ("POST", "/api/v1/subscription/members", {"user_id": ME, "full_name": "Me", "email": "me@x.com"}),
    ("POST", "/api/v1/subscription/swap-requests", {"member_profile_id": "m-mine", "requested_class": "luxury"}),
    ("POST", "/api/v1/payment/charges", {"user_id": ME, "amount_cents": 100}),
    ("GET", "/api/v1/payment/charges/c-mine", None),
    ("GET", "/api/v1/hub/hubs", None),  # ordinary read permission still applies
])
async def test_member_reaches_their_own_records(method, path, body):
    await _call(method, path, body)


@pytest.mark.parametrize("method,path,body", [
    ("GET", f"/api/v1/subscription/members/by-user-id/{OTHER}", None),
    ("GET", "/api/v1/subscription/members/m-theirs", None),
    ("GET", "/api/v1/subscription/members/m-theirs/gu-ledger", None),
    ("POST", "/api/v1/subscription/subscriptions/s-theirs/initiate-cancellation", {}),
    ("POST", "/api/v1/subscription/members", {"user_id": OTHER, "full_name": "X", "email": "x@x.com"}),
    ("POST", "/api/v1/subscription/gu/redeem", {"member_profile_id": "m-theirs", "catalog_item_key": "k"}),
    ("POST", "/api/v1/payment/charges", {"user_id": OTHER, "amount_cents": 100}),
    ("GET", "/api/v1/payment/charges/c-theirs", None),
    ("GET", "/api/v1/payment/charges/does-not-exist", None),
])
async def test_member_cannot_touch_anyone_elses(method, path, body):
    with pytest.raises(AuthorizationError) as exc:
        await _call(method, path, body)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/subscription/members"),                  # the whole member list
    ("POST", "/api/v1/subscription/plans"),                   # staff-only catalog change
    ("POST", "/api/v1/subscription/deposit-holds/h-1/release"),
    ("GET", "/api/v1/subscription/corporate/accounts"),
])
async def test_endpoints_outside_the_self_service_list_are_refused(method, path):
    with pytest.raises(AuthorizationError):
        await _call(method, path, {})


async def test_malformed_body_is_refused_not_crashed():
    with pytest.raises(AuthorizationError):
        await authorize(CLAIMS, resolve_route("/api/v1/payment/charges"), "POST", "/api/v1/payment/charges", b"not json")


async def test_staff_with_full_permission_skip_ownership_checks(monkeypatch):
    async def _staff(tenant_id, user_id):
        return frozenset({"subscription.*"})

    monkeypatch.setattr(authorization, "permissions_for", _staff)
    await _call("GET", "/api/v1/subscription/members/m-theirs")
    await _call("GET", "/api/v1/subscription/members")
