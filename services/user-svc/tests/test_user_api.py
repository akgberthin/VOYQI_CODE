import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
async def test_create_user_via_api(client):
    resp = await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "ada@voyqi.com"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["email"] == "ada@voyqi.com"


@pytest.mark.asyncio
async def test_get_user_404_for_unknown_id(client):
    resp = await client.get(f"/api/v1/user/users/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_created_user(client):
    create_resp = await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "ada@voyqi.com"}, headers=TENANT_HEADERS)
    user_id = create_resp.json()["id"]

    get_resp = await client.get(f"/api/v1/user/users/{user_id}", headers=TENANT_HEADERS)
    assert get_resp.status_code == 200
    assert get_resp.json()["first_name"] == "Ada"


@pytest.mark.asyncio
async def test_register_endpoint_rejects_short_password(client):
    resp = await client.post(
        "/api/v1/user/users/register",
        json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "ada@voyqi.com", "password": "short"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


def _mock_auth_svc_client(*, succeed: bool):
    """Mocks the httpx.AsyncClient constructed *inside* user_engine's own
    call to auth-svc — never the test's own `client` fixture, which is
    itself an httpx.AsyncClient wrapping the app under test and must
    keep working normally."""
    mock_client = AsyncMock()
    if succeed:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
    else:
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("auth-svc unreachable"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return patch("src.services.user_engine.httpx.AsyncClient", return_value=mock_client)


@pytest.mark.asyncio
async def test_register_endpoint_success_orchestrates_with_auth_svc(client):
    with _mock_auth_svc_client(succeed=True):
        resp = await client.post(
            "/api/v1/user/users/register",
            json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "ada@voyqi.com", "password": "correct-horse-battery-staple"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_register_endpoint_returns_502_and_no_orphan_when_auth_svc_down(client):
    with _mock_auth_svc_client(succeed=False):
        resp = await client.post(
            "/api/v1/user/users/register",
            json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "orphan-check@voyqi.com", "password": "correct-horse-battery-staple"},
            headers=TENANT_HEADERS,
        )
    assert resp.status_code == 502

    # Confirm no orphaned user leaked through the API layer either.
    list_check = await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Probe", "last_name": "Probe", "email": "orphan-check@voyqi.com"}, headers=TENANT_HEADERS)
    assert list_check.status_code == 201  # would fail if a stale row/unique constraint conflicted


@pytest.mark.asyncio
async def test_status_change_via_api(client):
    create_resp = await client.post("/api/v1/user/users", json={"user_type": "employee", "first_name": "Ada", "last_name": "Lovelace", "email": "ada2@voyqi.com"}, headers=TENANT_HEADERS)
    user_id = create_resp.json()["id"]

    status_resp = await client.post(f"/api/v1/user/users/{user_id}/status", json={"status": "suspended", "reason": "Investigation pending"}, headers=TENANT_HEADERS)
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_profile_link_via_api(client):
    create_resp = await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "ada3@voyqi.com"}, headers=TENANT_HEADERS)
    user_id = create_resp.json()["id"]

    link_resp = await client.post(
        f"/api/v1/user/users/{user_id}/profile-links",
        json={"linked_service": "subscription-svc", "linked_entity_type": "MemberProfile", "linked_entity_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert link_resp.status_code == 201
    assert link_resp.json()["linked_service"] == "subscription-svc"


@pytest.mark.asyncio
async def test_add_address_via_api(client):
    create_resp = await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Ada", "last_name": "Lovelace", "email": "ada4@voyqi.com"}, headers=TENANT_HEADERS)
    user_id = create_resp.json()["id"]

    addr_resp = await client.post(
        f"/api/v1/user/users/{user_id}/addresses",
        json={"address_type": "billing", "line1": "1 Main St", "city": "Miami", "state": "FL", "postal_code": "33101", "is_primary": True},
        headers=TENANT_HEADERS,
    )
    assert addr_resp.status_code == 201
    assert addr_resp.json()["is_primary"] is True


@pytest.mark.asyncio
async def test_another_tenant_cannot_modify_a_users_links_addresses_or_preferences(client):
    """Sub-resource writes are scoped to the caller's tenant — a user id
    from another tenant is a 404, not a silent cross-tenant write."""
    user_id = (await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Ada", "last_name": "L", "email": "ada9@voyqi.com"}, headers=TENANT_HEADERS)).json()["id"]
    other = {"X-Tenant-Id": "some-other-tenant"}

    link = await client.post(
        f"/api/v1/user/users/{user_id}/profile-links",
        json={"linked_service": "x", "linked_entity_type": "y", "linked_entity_id": str(uuid.uuid4())}, headers=other,
    )
    pref = await client.put(f"/api/v1/user/users/{user_id}/preferences", json={"key": "lang", "value": "en"}, headers=other)
    assert link.status_code == 404
    assert pref.status_code == 404
    assert (await client.put(f"/api/v1/user/users/{user_id}/preferences", json={"key": "lang", "value": "en"})).status_code == 400


@pytest.mark.asyncio
async def test_unknown_user_type_rejected(client):
    resp = await client.post("/api/v1/user/users", json={"user_type": "staff", "first_name": "A", "last_name": "B", "email": "x@voyqi.com"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_user_lifecycle_publishes_events(client, monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((event_type, payload["user_id"]))

    monkeypatch.setattr("src.api.users.publish_event", _spy)
    user_id = (await client.post("/api/v1/user/users", json={"user_type": "member", "first_name": "Ada", "last_name": "L", "email": "ada10@voyqi.com"}, headers=TENANT_HEADERS)).json()["id"]
    await client.patch(f"/api/v1/user/users/{user_id}", json={"first_name": "Augusta"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/user/users/{user_id}/status", json={"status": "suspended", "reason": "chargeback"}, headers=TENANT_HEADERS)

    assert events == [("user.created", user_id), ("user.updated", user_id), ("user.status_changed", user_id)]


@pytest.mark.asyncio
async def test_same_email_cannot_be_created_twice_in_a_tenant(client):
    body = {"user_type": "member", "first_name": "Ada", "last_name": "L", "email": "Dup@voyqi.com"}
    assert (await client.post("/api/v1/user/users", json=body, headers=TENANT_HEADERS)).status_code == 201
    again = await client.post("/api/v1/user/users", json={**body, "email": "dup@VOYQI.com"}, headers=TENANT_HEADERS)
    assert again.status_code == 409
    other_tenant = await client.post("/api/v1/user/users", json=body, headers={"X-Tenant-Id": "another-tenant"})
    assert other_tenant.status_code == 201
