import uuid

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import User, UserAddress, UserPreference, UserProfileLink

settings = get_settings()


class EmailAlreadyRegisteredError(Exception):
    pass


class RegistrationError(Exception):
    """Raised when the compound register flow fails — the User row this
    service created has already been rolled back (compensating delete)
    by the time this is raised, so the caller sees no orphan record."""


async def create_user(
    db: AsyncSession, *, tenant_id: str, user_type: str, first_name: str, last_name: str, email: str, phone: str | None = None,
) -> User:
    email_norm = email.lower().strip()
    existing = await db.execute(select(User.id).where(User.tenant_id == tenant_id, User.email == email_norm))
    if existing.first() is not None:
        raise EmailAlreadyRegisteredError("a user with this email already exists")
    user = User(tenant_id=tenant_id, user_type=user_type, first_name=first_name, last_name=last_name, email=email_norm, phone=phone)
    db.add(user)
    await db.flush()
    return user


async def register_user_with_credentials(
    db: AsyncSession, *, tenant_id: str, user_type: str, first_name: str, last_name: str, email: str, password: str, phone: str | None = None,
) -> User:
    """Compound operation: create the User record here, then register
    login credentials in auth-svc using the new user's id. If the
    auth-svc call fails for any reason, the just-created User row is
    deleted (a compensating action) rather than left as an orphan with
    no way to ever log in — there is no cross-service transaction, so
    this is the honest substitute for one."""
    user = await create_user(db, tenant_id=tenant_id, user_type=user_type, first_name=first_name, last_name=last_name, email=email, phone=phone)
    await db.flush()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.auth_svc_base_url}/api/v1/auth/register",
                headers={"X-Tenant-Id": tenant_id},
                json={"user_id": str(user.id), "email": email, "password": password},
            )
            if resp.status_code == 409:
                await db.delete(user)
                await db.flush()
                raise EmailAlreadyRegisteredError("a user with this email already exists")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        await db.delete(user)
        await db.flush()
        raise RegistrationError(f"credential registration failed, user record rolled back: {exc}") from exc

    return user


async def get_user(db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def update_user(db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, **fields) -> User | None:
    user = await get_user(db, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(user, key):
            setattr(user, key, value)
    await db.flush()
    return user


async def set_status(db: AsyncSession, *, tenant_id: str, user_id: uuid.UUID, status: str, reason: str | None = None) -> User | None:
    user = await get_user(db, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        return None
    user.status = status
    user.status_reason = reason
    await db.flush()
    return user


async def link_profile(db: AsyncSession, *, user_id: uuid.UUID, linked_service: str, linked_entity_type: str, linked_entity_id: uuid.UUID) -> UserProfileLink:
    result = await db.execute(
        select(UserProfileLink).where(
            UserProfileLink.user_id == user_id, UserProfileLink.linked_service == linked_service, UserProfileLink.linked_entity_type == linked_entity_type,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.linked_entity_id = linked_entity_id
        await db.flush()
        return existing
    link = UserProfileLink(user_id=user_id, linked_service=linked_service, linked_entity_type=linked_entity_type, linked_entity_id=linked_entity_id)
    db.add(link)
    await db.flush()
    return link


async def add_address(
    db: AsyncSession, *, user_id: uuid.UUID, tenant_id: str, address_type: str, line1: str, city: str, state: str,
    postal_code: str, line2: str | None = None, country: str = "US", is_primary: bool = False,
) -> UserAddress:
    if is_primary:
        # Only one primary address per (user, address_type) — unset any
        # existing one before inserting the new primary.
        await db.execute(
            update(UserAddress)
            .where(UserAddress.user_id == user_id, UserAddress.address_type == address_type, UserAddress.is_primary.is_(True))
            .values(is_primary=False)
        )
    address = UserAddress(
        user_id=user_id, tenant_id=tenant_id, address_type=address_type, line1=line1, line2=line2,
        city=city, state=state, postal_code=postal_code, country=country, is_primary=is_primary,
    )
    db.add(address)
    await db.flush()
    return address


async def set_preference(db: AsyncSession, *, user_id: uuid.UUID, key: str, value: str) -> UserPreference:
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user_id, UserPreference.key == key))
    pref = result.scalar_one_or_none()
    if pref is None:
        pref = UserPreference(user_id=user_id, key=key, value=value)
        db.add(pref)
    else:
        pref.value = value
    await db.flush()
    return pref
