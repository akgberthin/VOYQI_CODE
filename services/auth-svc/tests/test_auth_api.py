import uuid
from unittest.mock import AsyncMock, patch

import pyotp
import pytest

from tests.conftest import TENANT_HEADERS


async def _register_and_get_payload(client, email="member@voyqi.com", password="correct-horse-battery-staple"):
    resp = await client.post("/api/v1/auth/register", json={"user_id": str(uuid.uuid4()), "email": email, "password": password}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_register_requires_tenant_header(client):
    resp = await client.post("/api/v1/auth/register", json={"user_id": str(uuid.uuid4()), "email": "a@voyqi.com", "password": "correct-horse-battery-staple"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_register_rejects_short_password(client):
    resp = await client.post("/api/v1/auth/register", json={"user_id": str(uuid.uuid4()), "email": "a@voyqi.com", "password": "short"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_sets_httponly_cookies_on_success(client):
    await _register_and_get_payload(client)
    resp = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    set_cookie_headers = resp.headers.get_list("set-cookie")
    access_cookie = next((h for h in set_cookie_headers if h.startswith("access_token=")), None)
    refresh_cookie = next((h for h in set_cookie_headers if h.startswith("refresh_token=")), None)
    assert access_cookie is not None and "HttpOnly" in access_cookie
    assert refresh_cookie is not None and "HttpOnly" in refresh_cookie


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    await _register_and_get_payload(client)
    resp = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "wrong"}, headers=TENANT_HEADERS)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_lockout_returns_423(client):
    from src.core.config import get_settings
    settings = get_settings()
    await _register_and_get_payload(client)
    for _ in range(settings.max_failed_login_attempts):
        resp = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "wrong"}, headers=TENANT_HEADERS)
    assert resp.status_code == 423


@pytest.mark.asyncio
async def test_me_requires_valid_cookie(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_succeeds_with_valid_session_cookie(client):
    await _register_and_get_payload(client)
    login_resp = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    assert login_resp.status_code == 200

    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["tenant_id"] == TENANT_HEADERS["X-Tenant-Id"]


@pytest.mark.asyncio
async def test_refresh_rotates_cookies(client):
    await _register_and_get_payload(client)
    await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    old_refresh_cookie = client.cookies.get("refresh_token")

    refresh_resp = await client.post("/api/v1/auth/refresh", headers=TENANT_HEADERS)
    assert refresh_resp.status_code == 200
    new_refresh_cookie = client.cookies.get("refresh_token")
    assert new_refresh_cookie != old_refresh_cookie


@pytest.mark.asyncio
async def test_refresh_without_cookie_returns_401(client):
    resp = await client.post("/api/v1/auth/refresh", headers=TENANT_HEADERS)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_session_so_me_then_fails(client):
    await _register_and_get_payload(client)
    await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)

    logout_resp = await client.post("/api/v1/auth/logout", headers=TENANT_HEADERS)
    assert logout_resp.status_code == 200

    # Cookies were cleared client-side by delete_cookie, so a subsequent
    # /me call (which the httpx client will now send without them) fails.
    me_resp = await client.get("/api/v1/auth/me")
    assert me_resp.status_code == 401


@pytest.mark.asyncio
async def test_full_mfa_enrollment_and_login_flow(client):
    await _register_and_get_payload(client)
    await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)

    enroll_resp = await client.post("/api/v1/auth/mfa/enroll")
    assert enroll_resp.status_code == 200
    secret = pyotp.parse_uri(enroll_resp.json()["provisioning_uri"]).secret

    confirm_resp = await client.post("/api/v1/auth/mfa/confirm", json={"totp_code": pyotp.TOTP(secret).now()})
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["mfa_enabled"] is True

    # Log out, then log back in — should now require MFA.
    await client.post("/api/v1/auth/logout", headers=TENANT_HEADERS)
    login_resp = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    assert login_resp.status_code == 200
    body = login_resp.json()
    assert body["status"] == "mfa_required"
    user_credential_id = body["user_credential_id"]

    mfa_resp = await client.post("/api/v1/auth/mfa/verify", json={"user_credential_id": user_credential_id, "totp_code": pyotp.TOTP(secret).now()}, headers=TENANT_HEADERS)
    assert mfa_resp.status_code == 200
    assert mfa_resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_password_reset_end_to_end_with_notification_call_mocked(client):
    await _register_and_get_payload(client)

    with patch("src.services.notification_client.send_password_reset_email", new_callable=AsyncMock) as mock_send:
        request_resp = await client.post("/api/v1/auth/password-reset/request", json={"email": "member@voyqi.com"}, headers=TENANT_HEADERS)
        assert request_resp.status_code == 200
        assert mock_send.await_count == 1
        raw_token = mock_send.call_args.kwargs["raw_token"]

    confirm_resp = await client.post("/api/v1/auth/password-reset/confirm", json={"token": raw_token, "new_password": "brand-new-password-999"}, headers=TENANT_HEADERS)
    assert confirm_resp.status_code == 200

    old_login = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    assert old_login.status_code == 401

    new_login = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "brand-new-password-999"}, headers=TENANT_HEADERS)
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_request_does_not_reveal_unknown_email(client):
    """Same response whether the email exists or not — no user enumeration."""
    with patch("src.services.notification_client.send_password_reset_email", new_callable=AsyncMock):
        known_resp = await client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@voyqi.com"}, headers=TENANT_HEADERS)
    assert known_resp.status_code == 200
    assert known_resp.json()["status"] == "if_account_exists_email_sent"


@pytest.mark.asyncio
async def test_auth_security_events_never_carry_secrets(client, monkeypatch):
    """Failure paths raise HTTPException (no BackgroundTasks), so events
    are scheduled as tasks — this checks they still go out, and that no
    password or token ever appears in a payload."""
    import asyncio

    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("src.api.auth.publish_event", _spy)
    await _register_and_get_payload(client)
    ok = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "correct-horse-battery-staple"}, headers=TENANT_HEADERS)
    bad = await client.post("/api/v1/auth/login", json={"email": "member@voyqi.com", "password": "wrong-password-123"}, headers=TENANT_HEADERS)
    assert (ok.status_code, bad.status_code) == (200, 401)
    for _ in range(5):
        await asyncio.sleep(0)

    assert [e for e, _p in events] == ["auth.credential_registered", "auth.login_succeeded", "auth.login_failed"]
    flat = repr(events)
    assert "correct-horse-battery-staple" not in flat
    assert "wrong-password-123" not in flat
    token = ok.cookies.get("access_token")
    assert not token or token not in flat
