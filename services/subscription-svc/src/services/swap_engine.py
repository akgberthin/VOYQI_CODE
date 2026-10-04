"""
MOB-006 Vehicle Swap & Request Queue engine.

request_swap checks real class-eligible availability via fleet-svc
before creating the request; staging a fulfillment proposes a fleet-svc
TransferOrder — the same PROPOSED-not-dispatched contract the Fleet
Intelligence Agent's own tool uses, so a swap request never skips the
human-dispatch step just because it originated from a member request
instead of an AI recommendation.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import MobilityClass, SwapRequest, SwapRequestStatus
from src.services import fleet_tools


async def request_swap(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, requested_class: MobilityClass) -> SwapRequest:
    settings = get_settings()
    sla_due_at = datetime.now(timezone.utc) + timedelta(hours=settings.swap_sla_hours)
    request = SwapRequest(
        tenant_id=tenant_id, member_profile_id=member_profile_id, requested_class=requested_class, sla_due_at=sla_due_at,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return request


async def stage_fulfillment(db: AsyncSession, tenant_id: str, swap_request_id: uuid.UUID, to_hub_id: str) -> SwapRequest | None:
    result = await db.execute(select(SwapRequest).where(SwapRequest.id == swap_request_id, SwapRequest.tenant_id == tenant_id))
    request = result.scalar_one_or_none()
    if request is None or request.status != SwapRequestStatus.REQUESTED:
        return None

    available = fleet_tools.search_available_vehicles(tenant_id, request.requested_class.value, hub_id=to_hub_id)
    if not available:
        return request  # stays REQUESTED — nothing eligible to stage yet

    vehicle = available[0]
    transfer = fleet_tools.propose_transfer(
        tenant_id, vehicle["id"], to_hub_id, f"member swap request {swap_request_id}"
    )
    request.fleet_transfer_order_id = uuid.UUID(transfer["id"])
    request.status = SwapRequestStatus.STAGED
    await db.commit()
    await db.refresh(request)
    return request


async def fulfill_swap(db: AsyncSession, tenant_id: str, swap_request_id: uuid.UUID) -> SwapRequest | None:
    result = await db.execute(select(SwapRequest).where(SwapRequest.id == swap_request_id, SwapRequest.tenant_id == tenant_id))
    request = result.scalar_one_or_none()
    if request is None or request.status != SwapRequestStatus.STAGED:
        return None
    request.status = SwapRequestStatus.FULFILLED
    request.fulfilled_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(request)
    return request


async def check_sla_breach(db: AsyncSession, tenant_id: str, swap_request_id: uuid.UUID) -> SwapRequest | None:
    result = await db.execute(select(SwapRequest).where(SwapRequest.id == swap_request_id, SwapRequest.tenant_id == tenant_id))
    request = result.scalar_one_or_none()
    if request is None or request.status in (SwapRequestStatus.FULFILLED, SwapRequestStatus.CANCELLED):
        return request

    due = request.sla_due_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > due and request.status != SwapRequestStatus.SLA_BREACHED:
        request.status = SwapRequestStatus.SLA_BREACHED
        await db.commit()
        await db.refresh(request)
    return request


async def list_swap_requests(db: AsyncSession, tenant_id: str, status: SwapRequestStatus | None = None) -> list[SwapRequest]:
    stmt = select(SwapRequest).where(SwapRequest.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(SwapRequest.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())
