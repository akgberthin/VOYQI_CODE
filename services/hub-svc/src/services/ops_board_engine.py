"""
Hub Operations Board — HUB-002.

Two independent hard-gate/hard-signal mechanisms live here, of
different kinds:

1. mark_checklist_ready refuses while any PrepChecklistItem is still
   open — arithmetic/checklist discipline, the same shape as dms-svc's
   ReconOrder.mark_front_line_ready. No override; finish the checklist.

2. record_card_tap runs a geographic-plausibility check against the
   member's immediately preceding tap. An implied speed above what's
   physically possible (faster than any real flight+ground combination
   for a short gap) auto-suspends the card and publishes a hard
   security event to the Compliance/Audit Agent — no override exists in
   this pipeline either; reactivation is a distinct verification step,
   not an override of the finding.
"""
import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    CardStatus,
    CardTapEvent,
    ChecklistStatus,
    Hub,
    PrepChecklistItem,
    TouchpointEvent,
    TouchpointEventType,
    TouchpointSource,
    TouchpointStatus,
    VehiclePrepChecklist,
    VoyqiCard,
)


class ChecklistIncompleteError(ValueError):
    pass


# ── Touchpoint events ─────────────────────────────────────────────────

async def schedule_touchpoint(
    db: AsyncSession, tenant_id: str, hub_id: uuid.UUID, event_type: TouchpointEventType, source: TouchpointSource,
    member_or_renter_ref: str, vehicle_id: uuid.UUID | None = None,
) -> TouchpointEvent:
    event = TouchpointEvent(
        tenant_id=tenant_id, hub_id=hub_id, event_type=event_type, source=source,
        member_or_renter_ref=member_or_renter_ref, vehicle_id=vehicle_id,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def list_touchpoints_for_hub(
    db: AsyncSession, tenant_id: str, hub_id: uuid.UUID, status: TouchpointStatus | None = None
) -> list[TouchpointEvent]:
    stmt = select(TouchpointEvent).where(TouchpointEvent.tenant_id == tenant_id, TouchpointEvent.hub_id == hub_id)
    if status:
        stmt = stmt.where(TouchpointEvent.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def complete_touchpoint(db: AsyncSession, tenant_id: str, event_id: uuid.UUID) -> TouchpointEvent | None:
    result = await db.execute(select(TouchpointEvent).where(TouchpointEvent.id == event_id, TouchpointEvent.tenant_id == tenant_id))
    event = result.scalar_one_or_none()
    if event is None or event.status != TouchpointStatus.SCHEDULED:
        return None
    event.status = TouchpointStatus.COMPLETED
    event.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(event)
    return event


async def flag_exception(db: AsyncSession, tenant_id: str, event_id: uuid.UUID, reason: str) -> TouchpointEvent | None:
    result = await db.execute(select(TouchpointEvent).where(TouchpointEvent.id == event_id, TouchpointEvent.tenant_id == tenant_id))
    event = result.scalar_one_or_none()
    if event is None:
        return None
    event.status = TouchpointStatus.EXCEPTION
    event.exception_reason = reason
    await db.commit()
    await db.refresh(event)
    return event


# ── Prep checklists ───────────────────────────────────────────────────

async def open_checklist(
    db: AsyncSession, tenant_id: str, hub_id: uuid.UUID, vehicle_id: uuid.UUID,
    item_labels: list[str], touchpoint_event_id: uuid.UUID | None = None,
) -> VehiclePrepChecklist:
    checklist = VehiclePrepChecklist(tenant_id=tenant_id, hub_id=hub_id, vehicle_id=vehicle_id, touchpoint_event_id=touchpoint_event_id)
    db.add(checklist)
    await db.flush()
    for label in item_labels:
        db.add(PrepChecklistItem(tenant_id=tenant_id, checklist_id=checklist.id, label=label))
    await db.commit()
    await db.refresh(checklist)
    return checklist


async def list_checklist_items(db: AsyncSession, tenant_id: str, checklist_id: uuid.UUID) -> list[PrepChecklistItem]:
    result = await db.execute(select(PrepChecklistItem).where(PrepChecklistItem.checklist_id == checklist_id, PrepChecklistItem.tenant_id == tenant_id))
    return list(result.scalars().all())


async def check_item(db: AsyncSession, tenant_id: str, item_id: uuid.UUID) -> PrepChecklistItem | None:
    result = await db.execute(select(PrepChecklistItem).where(PrepChecklistItem.id == item_id, PrepChecklistItem.tenant_id == tenant_id))
    item = result.scalar_one_or_none()
    if item is None:
        return None
    item.is_completed = True
    await db.commit()
    await db.refresh(item)
    return item


async def mark_checklist_ready(db: AsyncSession, tenant_id: str, checklist_id: uuid.UUID) -> VehiclePrepChecklist:
    result = await db.execute(select(VehiclePrepChecklist).where(VehiclePrepChecklist.id == checklist_id, VehiclePrepChecklist.tenant_id == tenant_id))
    checklist = result.scalar_one_or_none()
    if checklist is None:
        raise ValueError(f"checklist {checklist_id} not found")

    items = await list_checklist_items(db, tenant_id, checklist_id)
    incomplete = [i for i in items if not i.is_completed]
    if incomplete:
        raise ChecklistIncompleteError(f"{len(incomplete)} checklist item(s) still open — every item must be checked first")

    checklist.status = ChecklistStatus.COMPLETED
    checklist.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(checklist)
    return checklist


# ── VOYQI Card taps + geographic anomaly detection ───────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def issue_card(db: AsyncSession, tenant_id: str, member_profile_id: str, card_number: str) -> VoyqiCard:
    card = VoyqiCard(tenant_id=tenant_id, member_profile_id=member_profile_id, card_number=card_number)
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return card


async def reactivate_card(db: AsyncSession, tenant_id: str, card_id: uuid.UUID) -> VoyqiCard | None:
    """A distinct human action — verifying the member out of band — not
    an override of the anomaly finding itself."""
    result = await db.execute(select(VoyqiCard).where(VoyqiCard.id == card_id, VoyqiCard.tenant_id == tenant_id))
    card = result.scalar_one_or_none()
    if card is None or card.status != CardStatus.SUSPENDED:
        return None
    card.status = CardStatus.ACTIVE
    await db.commit()
    await db.refresh(card)
    return card


async def list_taps_for_card(db: AsyncSession, tenant_id: str, card_id: uuid.UUID, limit: int = 20) -> list[CardTapEvent]:
    """Real tap history for one card — the Compliance/Audit Agent's tool
    for judging whether a flagged anomaly is a member's first or part of
    a repeat pattern, and whether the two hubs involved are ones the
    member visits normally."""
    result = await db.execute(
        select(CardTapEvent)
        .where(CardTapEvent.tenant_id == tenant_id, CardTapEvent.card_id == card_id)
        .order_by(CardTapEvent.tapped_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def record_card_tap(db: AsyncSession, tenant_id: str, hub_id: uuid.UUID, card_id: uuid.UUID) -> CardTapEvent:
    settings = get_settings()
    hub_result = await db.execute(select(Hub).where(Hub.id == hub_id, Hub.tenant_id == tenant_id))
    hub = hub_result.scalar_one_or_none()
    if hub is None:
        raise ValueError(f"hub {hub_id} not found")

    card_result = await db.execute(select(VoyqiCard).where(VoyqiCard.id == card_id, VoyqiCard.tenant_id == tenant_id))
    card = card_result.scalar_one_or_none()
    if card is None:
        raise ValueError(f"card {card_id} not found")

    last_tap_result = await db.execute(
        select(CardTapEvent)
        .where(CardTapEvent.tenant_id == tenant_id, CardTapEvent.card_id == card_id)
        .order_by(CardTapEvent.tapped_at.desc())
        .limit(1)
    )
    last_tap = last_tap_result.scalar_one_or_none()

    anomaly = False
    reason = ""
    now = datetime.now(timezone.utc)

    if last_tap is not None:
        last_hub_result = await db.execute(select(Hub).where(Hub.id == last_tap.hub_id))
        last_hub = last_hub_result.scalar_one_or_none()
        if last_hub is not None and last_hub.id != hub.id:
            distance_km = _haversine_km(last_hub.latitude, last_hub.longitude, hub.latitude, hub.longitude)
            last_tapped_at = last_tap.tapped_at
            if last_tapped_at.tzinfo is None:
                last_tapped_at = last_tapped_at.replace(tzinfo=timezone.utc)
            elapsed_hours = max((now - last_tapped_at).total_seconds() / 3600.0, 1 / 3600.0)  # floor at 1 second
            implied_speed_kmh = distance_km / elapsed_hours
            if implied_speed_kmh > settings.card_anomaly_max_plausible_kmh:
                anomaly = True
                reason = (
                    f"implied travel speed {implied_speed_kmh:,.0f} km/h between {last_hub.name} and {hub.name} "
                    f"({distance_km:,.0f} km in {elapsed_hours * 60:.1f} min) exceeds the "
                    f"{settings.card_anomaly_max_plausible_kmh:.0f} km/h plausibility threshold"
                )

    tap = CardTapEvent(tenant_id=tenant_id, hub_id=hub_id, card_id=card_id, tapped_at=now, anomaly_flagged=anomaly, anomaly_reason=reason)
    db.add(tap)
    if anomaly:
        card.status = CardStatus.SUSPENDED
    await db.commit()
    await db.refresh(tap)
    return tap
