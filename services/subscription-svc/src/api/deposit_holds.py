import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import DepositHoldStatus, MobilityClass
from src.services import deposit_engine
from src.services.deposit_engine import (
    DepositAmountUndefinedError,
    DepositNotPendingCaptureError,
    DepositNotPlacedError,
)

router = APIRouter(prefix="/api/v1/subscription", tags=["deposit-holds"])


class DepositHoldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    subscription_id: uuid.UUID | None
    mobility_class: MobilityClass
    amount_usd: float
    captured_amount_usd: float
    capture_reason: str
    status: DepositHoldStatus
    placed_at: datetime
    released_at: datetime | None
    resolved_at: datetime | None


class DepositHoldCreate(BaseModel):
    member_profile_id: uuid.UUID
    mobility_class: MobilityClass
    subscription_id: uuid.UUID | None = None


@router.post("/deposit-holds", response_model=DepositHoldOut, status_code=201)
async def place_deposit_hold(body: DepositHoldCreate, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Deterministic policy — the amount is a fixed table lookup by
    class, so this executes immediately with no approval step."""
    try:
        hold = await deposit_engine.place_deposit_hold(db, tenant_id, body.member_profile_id, body.mobility_class, body.subscription_id)
    except DepositAmountUndefinedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(
        publish_event, "subscription.deposit", "deposit.placed", tenant_id,
        {"hold_id": str(hold.id), "member_profile_id": str(body.member_profile_id), "amount_usd": hold.amount_usd},
    )
    return hold


@router.get("/deposit-holds/{hold_id}", response_model=DepositHoldOut)
async def get_deposit_hold(hold_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    hold = await deposit_engine.get_deposit_hold(db, tenant_id, hold_id)
    if hold is None:
        raise HTTPException(status_code=404, detail="Deposit hold not found")
    return hold


@router.get("/deposit-holds", response_model=list[DepositHoldOut])
async def list_deposit_holds(member_profile_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await deposit_engine.list_deposit_holds(db, tenant_id, member_profile_id)


@router.post("/deposit-holds/{hold_id}/release", response_model=DepositHoldOut)
async def release_deposit_hold(hold_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        hold = await deposit_engine.release_deposit_hold(db, tenant_id, hold_id)
    except DepositNotPlacedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(publish_event, "subscription.deposit", "deposit.released", tenant_id, {"hold_id": str(hold_id)})
    return hold


class CaptureRequest(BaseModel):
    amount_usd: float
    reason: str


@router.post("/deposit-holds/{hold_id}/request-capture", response_model=DepositHoldOut)
async def request_capture(hold_id: uuid.UUID, body: CaptureRequest, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Always lands in CAPTURE_PENDING_REVIEW regardless of the
    synchronous compliance review below — that call only adds a risk
    signal for the human reviewer, it never changes whether approval
    is required."""
    try:
        hold, evaluation = await deposit_engine.request_capture(db, tenant_id, hold_id, body.amount_usd, body.reason)
    except DepositNotPlacedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Compliance review already ran synchronously in request_capture;
    # the flag stops the Kafka consumer from reviewing it twice (it
    # still reviews it if the synchronous call failed).
    background_tasks.add_task(
        publish_event, "subscription.deposit", "deposit.capture_requested", tenant_id,
        {"hold_id": str(hold_id), "amount_usd": body.amount_usd, "reason": body.reason, "ai_evaluated_synchronously": evaluation is not None},
    )
    return hold


@router.post("/deposit-holds/{hold_id}/approve-capture", response_model=DepositHoldOut)
async def approve_capture(hold_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        hold = await deposit_engine.approve_capture(db, tenant_id, hold_id)
    except DepositNotPendingCaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(publish_event, "subscription.deposit", "deposit.captured", tenant_id, {"hold_id": str(hold_id), "amount_usd": hold.captured_amount_usd})
    return hold


@router.post("/deposit-holds/{hold_id}/reject-capture", response_model=DepositHoldOut)
async def reject_capture(hold_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await deposit_engine.reject_capture(db, tenant_id, hold_id)
    except DepositNotPendingCaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
