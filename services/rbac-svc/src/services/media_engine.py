"""SAP-006 — Media library engine. Fully autonomous: uploading and
categorizing an asset is a content operation, not access control."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import MediaAsset


async def create_asset(db: AsyncSession, tenant_id: str, filename: str, url: str, alt_text: str, category: str, uploaded_by: str) -> MediaAsset:
    asset = MediaAsset(tenant_id=tenant_id, filename=filename, url=url, alt_text=alt_text, category=category, uploaded_by=uploaded_by)
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def list_assets(db: AsyncSession, tenant_id: str, category: str | None = None) -> list[MediaAsset]:
    query = select(MediaAsset).where(MediaAsset.tenant_id == tenant_id)
    if category:
        query = query.where(MediaAsset.category == category)
    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_asset(db: AsyncSession, tenant_id: str, asset_id: uuid.UUID) -> bool:
    result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.tenant_id == tenant_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        return False
    await db.delete(asset)
    await db.commit()
    return True
