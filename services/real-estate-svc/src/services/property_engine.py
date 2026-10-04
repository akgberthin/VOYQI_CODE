import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Property, PropertyStatus, PropertyType


async def create_property(
    db: AsyncSession, tenant_id: str, key: str, name: str, property_type: PropertyType
) -> Property:
    prop = Property(tenant_id=tenant_id, key=key, name=name, property_type=property_type)
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


async def list_properties(
    db: AsyncSession, tenant_id: str, property_type: PropertyType | None = None
) -> list[Property]:
    stmt = select(Property).where(Property.tenant_id == tenant_id)
    if property_type:
        stmt = stmt.where(Property.property_type == property_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def deactivate_property(db: AsyncSession, tenant_id: str, property_id: uuid.UUID) -> Property | None:
    result = await db.execute(
        select(Property).where(Property.tenant_id == tenant_id, Property.id == property_id)
    )
    prop = result.scalar_one_or_none()
    if prop is None:
        return None
    prop.status = PropertyStatus.INACTIVE
    await db.commit()
    await db.refresh(prop)
    return prop
