"""
DMS-007 — Parts Inventory & Vendor Ordering engine.

evaluate_and_reorder places a vendor order the instant stock is at or
below the reorder point — no "place vendor order" click. Guarded by
last_reorder_stock_qty against re-ordering repeatedly at the same
stock level on repeated evaluation.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import Part


async def create_part(db: AsyncSession, tenant_id: str, part_number: str, name: str, unit_cost_usd: float, stock_qty: int, reorder_point: int) -> Part:
    part = Part(tenant_id=tenant_id, part_number=part_number, name=name, unit_cost_usd=unit_cost_usd, stock_qty=stock_qty, reorder_point=reorder_point)
    db.add(part)
    await db.commit()
    await db.refresh(part)
    return part


async def list_parts(db: AsyncSession, tenant_id: str) -> list[Part]:
    result = await db.execute(select(Part).where(Part.tenant_id == tenant_id))
    return list(result.scalars().all())


async def evaluate_and_reorder(db: AsyncSession, tenant_id: str) -> list[Part]:
    settings = get_settings()
    parts = await list_parts(db, tenant_id)
    reordered = []
    for part in parts:
        if part.stock_qty > part.reorder_point:
            continue
        if part.last_reorder_stock_qty == part.stock_qty:
            continue
        order_qty = max(settings.parts_reorder_minimum_qty, part.reorder_point * settings.parts_reorder_multiplier)
        part.stock_qty += order_qty
        part.last_reorder_stock_qty = part.stock_qty
        reordered.append(part)
    if reordered:
        await db.commit()
        for part in reordered:
            await db.refresh(part)
    return reordered
