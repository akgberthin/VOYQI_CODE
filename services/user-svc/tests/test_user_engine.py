import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from src.models import User, UserAddress
from src.services import user_engine

TENANT = "voyqi-holdings"


@pytest.mark.asyncio
async def test_create_user_basic(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="Ada@VOYQI.com")
    await db_session.commit()
    assert user.email == "ada@voyqi.com"  # normalized lowercase
    assert user.status == "active"


@pytest.mark.asyncio
async def test_get_user_scoped_to_tenant(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com")
    await db_session.commit()

    found = await user_engine.get_user(db_session, tenant_id=TENANT, user_id=user.id)
    assert found is not None

    wrong_tenant = await user_engine.get_user(db_session, tenant_id="other-tenant", user_id=user.id)
    assert wrong_tenant is None


@pytest.mark.asyncio
async def test_update_user_only_changes_provided_fields(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com", phone="555-0100")
    await db_session.commit()

    updated = await user_engine.update_user(db_session, tenant_id=TENANT, user_id=user.id, first_name="Augusta", last_name=None, phone=None)
    await db_session.commit()
    assert updated.first_name == "Augusta"
    assert updated.last_name == "Lovelace"  # unchanged
    assert updated.phone == "555-0100"  # unchanged


@pytest.mark.asyncio
async def test_set_status_records_reason(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="employee", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com")
    await db_session.commit()

    suspended = await user_engine.set_status(db_session, tenant_id=TENANT, user_id=user.id, status="suspended", reason="Offboarding in progress")
    await db_session.commit()
    assert suspended.status == "suspended"
    assert suspended.status_reason == "Offboarding in progress"


@pytest.mark.asyncio
async def test_link_profile_creates_then_updates_on_reuse(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com")
    await db_session.commit()
    member_profile_id = uuid.uuid4()

    link = await user_engine.link_profile(db_session, user_id=user.id, linked_service="subscription-svc", linked_entity_type="MemberProfile", linked_entity_id=member_profile_id)
    await db_session.commit()
    assert link.linked_entity_id == member_profile_id

    new_profile_id = uuid.uuid4()
    updated_link = await user_engine.link_profile(db_session, user_id=user.id, linked_service="subscription-svc", linked_entity_type="MemberProfile", linked_entity_id=new_profile_id)
    await db_session.commit()
    assert updated_link.id == link.id  # same row, updated in place
    assert updated_link.linked_entity_id == new_profile_id


@pytest.mark.asyncio
async def test_add_address_enforces_single_primary_per_type(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com")
    await db_session.commit()

    first = await user_engine.add_address(db_session, user_id=user.id, tenant_id=TENANT, address_type="billing", line1="1 Main St", city="Miami", state="FL", postal_code="33101", is_primary=True)
    await db_session.commit()
    second = await user_engine.add_address(db_session, user_id=user.id, tenant_id=TENANT, address_type="billing", line1="2 Ocean Dr", city="Miami", state="FL", postal_code="33139", is_primary=True)
    await db_session.commit()

    result = await db_session.execute(select(UserAddress).where(UserAddress.id == first.id))
    refreshed_first = result.scalar_one()
    assert refreshed_first.is_primary is False
    assert second.is_primary is True


@pytest.mark.asyncio
async def test_set_preference_upserts(db_session):
    user = await user_engine.create_user(db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com")
    await db_session.commit()

    await user_engine.set_preference(db_session, user_id=user.id, key="locale", value="en-US")
    await db_session.commit()
    updated = await user_engine.set_preference(db_session, user_id=user.id, key="locale", value="es-MX")
    await db_session.commit()
    assert updated.value == "es-MX"


@pytest.mark.asyncio
async def test_register_user_with_credentials_success_calls_auth_svc(db_session):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        user = await user_engine.register_user_with_credentials(
            db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com", password="correct-horse-battery-staple",
        )
        await db_session.commit()

    assert mock_post.await_count == 1
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["user_id"] == str(user.id)

    found = await user_engine.get_user(db_session, tenant_id=TENANT, user_id=user.id)
    assert found is not None


@pytest.mark.asyncio
async def test_register_user_rolls_back_user_record_when_auth_svc_fails(db_session):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx.ConnectError("auth-svc unreachable")):
        with pytest.raises(user_engine.RegistrationError):
            await user_engine.register_user_with_credentials(
                db_session, tenant_id=TENANT, user_type="member", first_name="Ada", last_name="Lovelace", email="ada@voyqi.com", password="correct-horse-battery-staple",
            )
        await db_session.commit()

    # No orphaned user record should remain.
    result = await db_session.execute(select(User).where(User.email == "ada@voyqi.com"))
    assert result.scalar_one_or_none() is None
