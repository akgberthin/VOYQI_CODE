import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import user_engine

router = APIRouter()


class CreateUserIn(BaseModel):
    user_type: str
    first_name: str
    last_name: str
    email: EmailStr
    phone: str | None = None


class RegisterUserIn(CreateUserIn):
    password: str


class UpdateUserIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class SetStatusIn(BaseModel):
    status: str
    reason: str | None = None


class LinkProfileIn(BaseModel):
    linked_service: str
    linked_entity_type: str
    linked_entity_id: uuid.UUID


class AddAddressIn(BaseModel):
    address_type: str
    line1: str
    line2: str | None = None
    city: str
    state: str
    postal_code: str
    country: str = "US"
    is_primary: bool = False


class SetPreferenceIn(BaseModel):
    key: str
    value: str


def _serialize(user) -> dict:
    return {
        "id": user.id, "user_type": user.user_type, "status": user.status,
        "first_name": user.first_name, "last_name": user.last_name, "email": user.email, "phone": user.phone,
    }


@router.post("/users", status_code=201)
async def create_user(payload: CreateUserIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    user = await user_engine.create_user(db, tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    return _serialize(user)


@router.post("/users/register", status_code=201)
async def register_user(payload: RegisterUserIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    if len(payload.password) < 10:
        raise HTTPException(status_code=422, detail="Password must be at least 10 characters")
    try:
        user = await user_engine.register_user_with_credentials(db, tenant_id=tenant_id, **payload.model_dump())
    except user_engine.RegistrationError as exc:
        await db.commit()  # commits the compensating delete
        raise HTTPException(status_code=502, detail=str(exc))
    await db.commit()
    return _serialize(user)


@router.get("/users/{user_id}")
async def get_user(user_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    user = await user_engine.get_user(db, tenant_id=tenant_id, user_id=user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _serialize(user)


@router.patch("/users/{user_id}")
async def update_user(user_id: uuid.UUID, payload: UpdateUserIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    user = await user_engine.update_user(db, tenant_id=tenant_id, user_id=user_id, **payload.model_dump())
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await db.commit()
    return _serialize(user)


@router.post("/users/{user_id}/status")
async def set_status(user_id: uuid.UUID, payload: SetStatusIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    user = await user_engine.set_status(db, tenant_id=tenant_id, user_id=user_id, status=payload.status, reason=payload.reason)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await db.commit()
    return _serialize(user)


@router.post("/users/{user_id}/profile-links", status_code=201)
async def link_profile(user_id: uuid.UUID, payload: LinkProfileIn, db: AsyncSession = Depends(get_db)):
    link = await user_engine.link_profile(db, user_id=user_id, **payload.model_dump())
    await db.commit()
    return {"id": link.id, "linked_service": link.linked_service, "linked_entity_type": link.linked_entity_type, "linked_entity_id": link.linked_entity_id}


@router.post("/users/{user_id}/addresses", status_code=201)
async def add_address(user_id: uuid.UUID, payload: AddAddressIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    address = await user_engine.add_address(db, user_id=user_id, tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    return {"id": address.id, "address_type": address.address_type, "is_primary": address.is_primary}


@router.put("/users/{user_id}/preferences")
async def set_preference(user_id: uuid.UUID, payload: SetPreferenceIn, db: AsyncSession = Depends(get_db)):
    pref = await user_engine.set_preference(db, user_id=user_id, key=payload.key, value=payload.value)
    await db.commit()
    return {"key": pref.key, "value": pref.value}
