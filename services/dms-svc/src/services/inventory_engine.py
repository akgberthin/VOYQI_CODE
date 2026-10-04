"""
DMS-002 — Retail Inventory & Merchandising engine.

Full automation throughout: apply_price is the terminal action for
every pricing path (manual re-price, the aging sweep, and the Pricing
Intelligence Agent's evaluate_retail_pricing) — there is no separate
"recommended price" a human has to click to accept. Title-brand
disclosure is drafted and applied autonomously too; it is a
consumer-protection/legal obligation, not a security matter.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    BookValueQuote,
    PricingRecommendation,
    TitleStatus,
    Vehicle,
    VehicleHistoryReport,
    VehicleStatus,
)


async def create_vehicle(
    db: AsyncSession, tenant_id: str, rooftop_id: uuid.UUID, vin: str, description: str,
    condition: str, price_usd: float, cost_usd: float, days_in_inventory: int = 0,
) -> Vehicle:
    vehicle = Vehicle(
        tenant_id=tenant_id, rooftop_id=rooftop_id, vin=vin, description=description,
        condition=condition, price_usd=price_usd, cost_usd=cost_usd, days_in_inventory=days_in_inventory,
    )
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def get_vehicle(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> Vehicle | None:
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def get_vehicle_by_vin(db: AsyncSession, tenant_id: str, vin: str) -> Vehicle | None:
    result = await db.execute(select(Vehicle).where(Vehicle.vin == vin, Vehicle.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_vehicles(db: AsyncSession, tenant_id: str) -> list[Vehicle]:
    result = await db.execute(select(Vehicle).where(Vehicle.tenant_id == tenant_id))
    return list(result.scalars().all())


async def apply_price(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, price_usd: float, reasoning: str) -> Vehicle:
    """The terminal action for every pricing path on this platform —
    no separate acceptance step. Every call is logged to
    PricingRecommendation as a permanent audit trail of an
    already-applied price, not a pending suggestion."""
    vehicle = await get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")
    vehicle.price_usd = price_usd
    db.add(PricingRecommendation(tenant_id=tenant_id, vehicle_id=vehicle_id, price_usd=price_usd, reasoning=reasoning))
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def add_book_value_quote(
    db: AsyncSession, tenant_id: str, vin: str, source: str, wholesale_usd: float, retail_usd: float,
) -> BookValueQuote:
    quote = BookValueQuote(tenant_id=tenant_id, vin=vin, source=source, wholesale_usd=wholesale_usd, retail_usd=retail_usd)
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return quote


async def get_book_value_average(db: AsyncSession, tenant_id: str, vin: str) -> dict:
    result = await db.execute(select(BookValueQuote).where(BookValueQuote.vin == vin, BookValueQuote.tenant_id == tenant_id))
    quotes = list(result.scalars().all())
    if not quotes:
        return {"average_retail_value_usd": None, "average_wholesale_value_usd": None}
    return {
        "average_retail_value_usd": sum(q.retail_usd for q in quotes) / len(quotes),
        "average_wholesale_value_usd": sum(q.wholesale_usd for q in quotes) / len(quotes),
    }


def compute_aging_markdown(anchor_price: float, days_in_inventory: int) -> tuple[float, float]:
    """Returns (new_price, markdown_pct). Pure arithmetic — the same
    auditable formula whether triggered by the sweep or a manual
    re-price request."""
    settings = get_settings()
    days_over = max(0, days_in_inventory - settings.aging_markdown_threshold_days)
    markdown_pct = min(settings.aging_markdown_cap_pct, days_over * settings.aging_markdown_pct_per_day)
    return round(anchor_price * (1 - markdown_pct), 2), markdown_pct


async def evaluate_aging_sweep(db: AsyncSession, tenant_id: str) -> list[Vehicle]:
    """Fully autonomous: every in-stock vehicle at or beyond the aging
    threshold gets its markdown applied in this call, guarded by
    last_markdown_days against re-applying at the same aging value on
    repeated evaluation. Returns the vehicles actually marked down."""
    settings = get_settings()
    vehicles = await list_vehicles(db, tenant_id)
    marked_down = []
    for vehicle in vehicles:
        if vehicle.status != VehicleStatus.IN_STOCK or vehicle.days_in_inventory < settings.aging_markdown_threshold_days:
            continue
        if vehicle.last_markdown_days == vehicle.days_in_inventory:
            continue
        anchor = vehicle.pre_markdown_price_usd or vehicle.price_usd
        if vehicle.pre_markdown_price_usd is None:
            vehicle.pre_markdown_price_usd = vehicle.price_usd
        book_avg = await get_book_value_average(db, tenant_id, vehicle.vin)
        if book_avg["average_retail_value_usd"] is not None:
            anchor = book_avg["average_retail_value_usd"]
        new_price, markdown_pct = compute_aging_markdown(anchor, vehicle.days_in_inventory)
        if new_price == vehicle.price_usd:
            vehicle.last_markdown_days = vehicle.days_in_inventory
            continue
        vehicle.price_usd = new_price
        vehicle.last_markdown_days = vehicle.days_in_inventory
        db.add(PricingRecommendation(
            tenant_id=tenant_id, vehicle_id=vehicle.id, price_usd=new_price,
            reasoning=f"[deterministic] Autonomous aging sweep — {vehicle.days_in_inventory} days in stock, "
                      f"{markdown_pct*100:.1f}% markdown applied against anchor ${anchor:,.2f}.",
        ))
        marked_down.append(vehicle)
    if marked_down:
        await db.commit()
        for vehicle in marked_down:
            await db.refresh(vehicle)
    return marked_down


async def apply_accelerated_markdown(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, threshold_days: int, markdown_cap_pct: float, markdown_pct_per_day: float) -> Vehicle | None:
    """The P&L capstone's intervention action — a tighter aging
    threshold than the standard sweep, applied to a specific rooftop's
    inventory when that rooftop is verdicted INTERVENE. Guarded by
    last_markdown_days exactly like evaluate_aging_sweep — without
    this, calling the capstone endpoint twice would compound the
    discount on a vehicle whose aging value hasn't actually changed."""
    vehicle = await get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None or vehicle.status != VehicleStatus.IN_STOCK or vehicle.days_in_inventory < threshold_days:
        return None
    if vehicle.last_markdown_days == vehicle.days_in_inventory:
        return None
    days_over = max(0, vehicle.days_in_inventory - threshold_days)
    markdown_pct = min(markdown_cap_pct, days_over * markdown_pct_per_day)
    new_price = round(vehicle.price_usd * (1 - markdown_pct), 2)
    vehicle.price_usd = new_price
    vehicle.last_markdown_days = vehicle.days_in_inventory
    db.add(PricingRecommendation(
        tenant_id=tenant_id, vehicle_id=vehicle_id, price_usd=new_price,
        reasoning=f"[deterministic] P&L capstone intervention — accelerated {threshold_days}-day threshold, {markdown_pct*100:.1f}% markdown.",
    ))
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


async def pull_vhr(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, provider: str, title_status: str,
    accident_count: int, odometer_rollback: bool,
) -> tuple[VehicleHistoryReport, Vehicle]:
    """Fully autonomous: pulling the report immediately drafts a
    disclosure (if there's a title issue) and syncs the listing — no
    separate click. Title-brand disclosure is a legal/consumer-
    protection obligation, not a security matter, so it's never gated
    behind a human-typed override."""
    vehicle = await get_vehicle(db, tenant_id, vehicle_id)
    if vehicle is None:
        raise ValueError(f"vehicle {vehicle_id} not found")

    has_issue = title_status != TitleStatus.CLEAN.value or odometer_rollback
    disclosure_text = ""
    if has_issue:
        issues = []
        if title_status != TitleStatus.CLEAN.value:
            issues.append(f"{title_status} title")
        if odometer_rollback:
            issues.append("odometer rollback")
        disclosure_text = (
            f"This vehicle is being sold with a disclosed {' and '.join(issues)}. Buyer acknowledges this "
            f"disclosure prior to purchase, per {provider} vehicle history report ({accident_count} accident(s) on record)."
        )

    report = VehicleHistoryReport(
        tenant_id=tenant_id, vin=vehicle.vin, provider=provider, title_status=title_status,
        accident_count=accident_count, odometer_rollback=odometer_rollback,
        disclosure_text=disclosure_text, listing_synced=True,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    await db.refresh(vehicle)
    return report, vehicle


async def get_latest_vhr(db: AsyncSession, tenant_id: str, vin: str) -> VehicleHistoryReport | None:
    result = await db.execute(
        select(VehicleHistoryReport).where(VehicleHistoryReport.vin == vin, VehicleHistoryReport.tenant_id == tenant_id)
        .order_by(VehicleHistoryReport.created_at.desc())
    )
    return result.scalars().first()
