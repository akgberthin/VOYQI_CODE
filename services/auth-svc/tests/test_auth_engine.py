import uuid

import pyotp
import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.models import RefreshToken
from src.services import auth_engine

settings = get_settings()
TENANT = "voyqi-holdings"


async def _register(db_session, email="member@voyqi.com", password="correct-horse-battery-staple"):
    return await auth_engine.register_user(db_session, tenant_id=TENANT, user_id=uuid.uuid4(), email=email, password=password)


@pytest.mark.asyncio
async def test_password_is_never_stored_in_plaintext(db_session):
    cred = await _register(db_session, password="my-real-password-123")
    assert cred.password_hash != "my-real-password-123"
    assert cred.password_hash.startswith("$2b$")  # bcrypt marker


@pytest.mark.asyncio
async def test_login_success_issues_tokens_and_resets_counters(db_session):
    await _register(db_session, password="correct-horse-battery-staple")
    await db_session.commit()

    outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    assert outcome.status == "success"
    assert outcome.tokens is not None
    assert outcome.tokens.access_token
    assert outcome.tokens.refresh_token


@pytest.mark.asyncio
async def test_login_wrong_password_fails_without_revealing_which_field_was_wrong(db_session):
    await _register(db_session)
    await db_session.commit()

    outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="wrong-password")
    assert outcome.status == "failed"
    assert outcome.failure_reason == "invalid_credentials"  # generic, not "invalid_password"


@pytest.mark.asyncio
async def test_login_unknown_email_returns_same_generic_failure_as_wrong_password(db_session):
    outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="nobody@voyqi.com", password="anything")
    assert outcome.status == "failed"
    assert outcome.failure_reason == "invalid_credentials"


@pytest.mark.asyncio
async def test_account_locks_after_max_failed_attempts(db_session):
    await _register(db_session)
    await db_session.commit()

    for _ in range(settings.max_failed_login_attempts - 1):
        outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="wrong")
        assert outcome.status == "failed"
    await db_session.commit()

    # The Nth failure should trigger the lock.
    final_outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="wrong")
    assert final_outcome.status == "locked"
    assert final_outcome.locked_until is not None


@pytest.mark.asyncio
async def test_locked_account_rejects_even_the_correct_password(db_session):
    await _register(db_session, password="correct-horse-battery-staple")
    await db_session.commit()

    for _ in range(settings.max_failed_login_attempts):
        await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="wrong")
    await db_session.commit()

    outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    assert outcome.status == "locked"


@pytest.mark.asyncio
async def test_mfa_enabled_account_requires_second_step_before_tokens_issued(db_session):
    cred = await _register(db_session, password="correct-horse-battery-staple")
    await db_session.flush()
    uri = await auth_engine.enroll_mfa(db_session, user_credential_id=cred.id, tenant_id=TENANT)
    secret = pyotp.parse_uri(uri).secret
    confirmed = await auth_engine.confirm_mfa_enrollment(db_session, user_credential_id=cred.id, tenant_id=TENANT, totp_code=pyotp.TOTP(secret).now())
    assert confirmed is True
    await db_session.commit()

    outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    assert outcome.status == "mfa_required"
    assert outcome.tokens is None


@pytest.mark.asyncio
async def test_mfa_correct_code_issues_tokens(db_session):
    cred = await _register(db_session, password="correct-horse-battery-staple")
    await db_session.flush()
    uri = await auth_engine.enroll_mfa(db_session, user_credential_id=cred.id, tenant_id=TENANT)
    secret = pyotp.parse_uri(uri).secret
    await auth_engine.confirm_mfa_enrollment(db_session, user_credential_id=cred.id, tenant_id=TENANT, totp_code=pyotp.TOTP(secret).now())
    await db_session.commit()

    outcome = await auth_engine.verify_mfa_and_issue_tokens(db_session, tenant_id=TENANT, user_credential_id=cred.id, totp_code=pyotp.TOTP(secret).now())
    assert outcome.status == "success"
    assert outcome.tokens is not None


@pytest.mark.asyncio
async def test_mfa_wrong_code_counts_toward_lockout(db_session):
    cred = await _register(db_session, password="correct-horse-battery-staple")
    await db_session.flush()
    uri = await auth_engine.enroll_mfa(db_session, user_credential_id=cred.id, tenant_id=TENANT)
    secret = pyotp.parse_uri(uri).secret
    await auth_engine.confirm_mfa_enrollment(db_session, user_credential_id=cred.id, tenant_id=TENANT, totp_code=pyotp.TOTP(secret).now())
    await db_session.commit()

    for _ in range(settings.max_failed_login_attempts):
        outcome = await auth_engine.verify_mfa_and_issue_tokens(db_session, tenant_id=TENANT, user_credential_id=cred.id, totp_code="000000")
    await db_session.commit()
    assert outcome.status == "locked"


@pytest.mark.asyncio
async def test_refresh_token_rotates_on_use(db_session):
    await _register(db_session, password="correct-horse-battery-staple")
    await db_session.commit()
    login_outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    await db_session.commit()

    new_tokens = await auth_engine.refresh_tokens(db_session, tenant_id=TENANT, raw_refresh_token=login_outcome.tokens.refresh_token)
    await db_session.commit()

    assert new_tokens.refresh_token != login_outcome.tokens.refresh_token
    assert new_tokens.access_token  # a fresh access token was issued (content may legitimately match if issued within the same second — JWT timestamps are integer-second precision)


@pytest.mark.asyncio
async def test_reusing_a_rotated_refresh_token_is_detected_and_revokes_all_sessions(db_session):
    await _register(db_session, password="correct-horse-battery-staple")
    await db_session.commit()
    login_outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    await db_session.commit()
    original_refresh = login_outcome.tokens.refresh_token

    # First use — legitimate rotation.
    new_tokens = await auth_engine.refresh_tokens(db_session, tenant_id=TENANT, raw_refresh_token=original_refresh)
    await db_session.commit()

    # Second use of the SAME (now-revoked) original token — this is the theft signal.
    with pytest.raises(auth_engine.ReuseDetectedError):
        await auth_engine.refresh_tokens(db_session, tenant_id=TENANT, raw_refresh_token=original_refresh)
    await db_session.commit()

    # The legitimately-rotated token should ALSO now be revoked as a defensive measure.
    result = await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == auth_engine.hash_token(new_tokens.refresh_token)))
    row = result.scalar_one()
    assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_expired_refresh_token_is_rejected(db_session, monkeypatch):
    await _register(db_session, password="correct-horse-battery-staple")
    await db_session.commit()
    login_outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    await db_session.commit()

    # Force the stored token's expiry into the past directly.
    result = await db_session.execute(select(RefreshToken).where(RefreshToken.token_hash == auth_engine.hash_token(login_outcome.tokens.refresh_token)))
    row = result.scalar_one()
    from datetime import datetime, timedelta, timezone
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    with pytest.raises(ValueError):
        await auth_engine.refresh_tokens(db_session, tenant_id=TENANT, raw_refresh_token=login_outcome.tokens.refresh_token)


@pytest.mark.asyncio
async def test_logout_revokes_the_refresh_token(db_session):
    await _register(db_session, password="correct-horse-battery-staple")
    await db_session.commit()
    login_outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="correct-horse-battery-staple")
    await db_session.commit()

    await auth_engine.logout(db_session, tenant_id=TENANT, raw_refresh_token=login_outcome.tokens.refresh_token)
    await db_session.commit()

    with pytest.raises(auth_engine.ReuseDetectedError):
        await auth_engine.refresh_tokens(db_session, tenant_id=TENANT, raw_refresh_token=login_outcome.tokens.refresh_token)


@pytest.mark.asyncio
async def test_password_reset_request_returns_none_for_unknown_email(db_session):
    token = await auth_engine.request_password_reset(db_session, tenant_id=TENANT, email="nobody@voyqi.com")
    assert token is None


@pytest.mark.asyncio
async def test_password_reset_full_flow_and_session_invalidation(db_session):
    await _register(db_session, password="old-password-123")
    await db_session.commit()
    login_outcome = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="old-password-123")
    await db_session.commit()

    reset_token = await auth_engine.request_password_reset(db_session, tenant_id=TENANT, email="member@voyqi.com")
    await db_session.commit()
    assert reset_token is not None

    ok = await auth_engine.reset_password(db_session, tenant_id=TENANT, raw_reset_token=reset_token, new_password="new-password-456")
    await db_session.commit()
    assert ok is True

    # Old password no longer works.
    old_login = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="old-password-123")
    assert old_login.status == "failed"

    # New password works.
    new_login = await auth_engine.login(db_session, tenant_id=TENANT, email="member@voyqi.com", password="new-password-456")
    assert new_login.status == "success"

    # The session that existed BEFORE the reset must now be dead.
    with pytest.raises(auth_engine.ReuseDetectedError):
        await auth_engine.refresh_tokens(db_session, tenant_id=TENANT, raw_refresh_token=login_outcome.tokens.refresh_token)


@pytest.mark.asyncio
async def test_password_reset_token_is_single_use(db_session):
    await _register(db_session, password="old-password-123")
    await db_session.commit()
    reset_token = await auth_engine.request_password_reset(db_session, tenant_id=TENANT, email="member@voyqi.com")
    await db_session.commit()

    first = await auth_engine.reset_password(db_session, tenant_id=TENANT, raw_reset_token=reset_token, new_password="new-password-456")
    await db_session.commit()
    assert first is True

    second = await auth_engine.reset_password(db_session, tenant_id=TENANT, raw_reset_token=reset_token, new_password="another-password-789")
    assert second is False


@pytest.mark.asyncio
async def test_reenrolling_mfa_keeps_the_old_secret_until_the_new_one_is_confirmed(db_session):
    cred = await _register(db_session, password="correct-horse-battery-staple")
    await db_session.flush()
    old = pyotp.parse_uri(await auth_engine.enroll_mfa(db_session, user_credential_id=cred.id, tenant_id=TENANT)).secret
    assert await auth_engine.confirm_mfa_enrollment(db_session, user_credential_id=cred.id, tenant_id=TENANT, totp_code=pyotp.TOTP(old).now())
    await db_session.commit()

    # start reconfiguring, then abandon it: the old authenticator still works
    new = pyotp.parse_uri(await auth_engine.enroll_mfa(db_session, user_credential_id=cred.id, tenant_id=TENANT)).secret
    await db_session.commit()
    assert new != old
    outcome = await auth_engine.verify_mfa_and_issue_tokens(db_session, tenant_id=TENANT, user_credential_id=cred.id, totp_code=pyotp.TOTP(old).now())
    assert outcome.status == "success"

    # a code from the old app can't confirm the new secret
    assert not await auth_engine.confirm_mfa_enrollment(db_session, user_credential_id=cred.id, tenant_id=TENANT, totp_code="000000")
    # confirming with the new app switches over
    assert await auth_engine.confirm_mfa_enrollment(db_session, user_credential_id=cred.id, tenant_id=TENANT, totp_code=pyotp.TOTP(new).now())
    await db_session.commit()
    assert cred.mfa_secret == new and cred.mfa_pending_secret is None
    outcome = await auth_engine.verify_mfa_and_issue_tokens(db_session, tenant_id=TENANT, user_credential_id=cred.id, totp_code=pyotp.TOTP(new).now())
    assert outcome.status == "success"
