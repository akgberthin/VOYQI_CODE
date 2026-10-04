import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Hub, HubType


async def create_hub(
    db: AsyncSession, tenant_id: str, name: str, hub_type: HubType, latitude: float, longitude: float,
    address: str = "", capacity: int = 10, operating_hours_open: str = "06:00", operating_hours_close: str = "22:00",
    has_lounge: bool = False, amenities: list | None = None, tenant_entity_ref: str = "",
) -> Hub:
    hub = Hub(
        tenant_id=tenant_id, name=name, hub_type=hub_type, latitude=latitude, longitude=longitude,
        address=address, capacity=capacity, operating_hours_open=operating_hours_open,
        operating_hours_close=operating_hours_close, has_lounge=has_lounge,
        amenities=amenities or [], tenant_entity_ref=tenant_entity_ref,
    )
    db.add(hub)
    await db.commit()
    await db.refresh(hub)
    return hub


async def get_hub(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID) -> Hub | None:
    result = await db.execute(select(Hub).where(Hub.id == hub_id, Hub.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_hubs(
    db: AsyncSession, tenant_id: str, hub_type: HubType | None = None, is_active: bool | None = None
) -> list[Hub]:
    stmt = select(Hub).where(Hub.tenant_id == tenant_id)
    if hub_type:
        stmt = stmt.where(Hub.hub_type == hub_type)
    if is_active is not None:
        stmt = stmt.where(Hub.is_active == is_active)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_capacity(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID, capacity: int) -> Hub | None:
    hub = await get_hub(db, tenant_id, hub_id)
    if hub is None:
        return None
    hub.capacity = capacity
    await db.commit()
    await db.refresh(hub)
    return hub


async def deactivate_hub(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID) -> Hub | None:
    hub = await get_hub(db, tenant_id, hub_id)
    if hub is None:
        return None
    hub.is_active = False
    await db.commit()
    await db.refresh(hub)
    return hub
