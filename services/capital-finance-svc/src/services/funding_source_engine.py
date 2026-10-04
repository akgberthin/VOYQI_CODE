import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import FundingSource, FundingSourceStatus, FundingSourceType


async def create_funding_source(
    db: AsyncSession, tenant_id: str, key: str, name: str, source_type: FundingSourceType
) -> FundingSource:
    source = FundingSource(tenant_id=tenant_id, key=key, name=name, source_type=source_type)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def list_funding_sources(
    db: AsyncSession, tenant_id: str, source_type: FundingSourceType | None = None
) -> list[FundingSource]:
    stmt = select(FundingSource).where(FundingSource.tenant_id == tenant_id)
    if source_type:
        stmt = stmt.where(FundingSource.source_type == source_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def deactivate_funding_source(
    db: AsyncSession, tenant_id: str, funding_source_id: uuid.UUID
) -> FundingSource | None:
    result = await db.execute(
        select(FundingSource).where(FundingSource.tenant_id == tenant_id, FundingSource.id == funding_source_id)
    )
    source = result.scalar_one_or_none()
    if source is None:
        return None
    source.status = FundingSourceStatus.INACTIVE
    await db.commit()
    await db.refresh(source)
    return source
