import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import SubscriptionStatus
from src.services import lifecycle_engine

router = APIRouter(prefix="/api/v1/subscription", tags=["lifecycle"])


class SubscriptionCreate(BaseModel):
    member_profile_id: uuid.UUID
    plan_id: uuid.UUID


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    plan_id: uuid.UUID
    status: SubscriptionStatus
    paused_at: datetime | None
    resume_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str
    win_back_offer_extended: bool


class PauseRequest(BaseModel):
    resume_at: datetime


class CancelInitiateRequest(BaseModel):
    reason: str


class CancelFinalizeRequest(BaseModel):
    reason: str
    exit_survey: dict = {}


@router.post("/subscriptions", response_model=SubscriptionOut, status_code=201)
async def create_subscription(
    body: SubscriptionCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    subscription = await lifecycle_engine.create_subscription(db, tenant_id, body.member_profile_id, body.plan_id)
    background_tasks.add_task(
        publish_event,
        "subscription.lifecycle",
        "subscription.created",
        tenant_id,
        {
            "subscription_id": str(subscription.id),
            "member_profile_id": str(body.member_profile_id),
            "plan_id": str(body.plan_id),
        },
    )
    return subscription


@router.get("/subscriptions/{subscription_id}", response_model=SubscriptionOut)
async def get_subscription(subscription_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Also the lookup the gateway uses to confirm a member owns the
    subscription they're acting on."""
    subscription = await lifecycle_engine.get_subscription(db, tenant_id, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return subscription


@router.post("/subscriptions/{subscription_id}/pause", response_model=SubscriptionOut)
async def pause(subscription_id: uuid.UUID, body: PauseRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    sub = await lifecycle_engine.pause_subscription(db, tenant_id, subscription_id, body.resume_at)
    if sub is None:
        raise HTTPException(status_code=409, detail="Subscription not found or not active")
    return sub


@router.post("/subscriptions/{subscription_id}/resume", response_model=SubscriptionOut)
async def resume(subscription_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    sub = await lifecycle_engine.resume_subscription(db, tenant_id, subscription_id)
    if sub is None:
        raise HTTPException(status_code=409, detail="Subscription not found or not paused")
    return sub


@router.post("/subscriptions/{subscription_id}/initiate-cancellation")
async def initiate_cancellation(subscription_id: uuid.UUID, body: CancelInitiateRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await lifecycle_engine.initiate_cancellation(db, tenant_id, subscription_id, body.reason)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return result


@router.post("/subscriptions/{subscription_id}/finalize-cancellation", response_model=SubscriptionOut)
async def finalize_cancellation(subscription_id: uuid.UUID, body: CancelFinalizeRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    sub = await lifecycle_engine.finalize_cancellation(db, tenant_id, subscription_id, body.reason, body.exit_survey)
    if sub is None:
        raise HTTPException(status_code=409, detail="Subscription not found or already cancelled")
    return sub


@router.post("/subscriptions/{subscription_id}/accept-winback", response_model=SubscriptionOut)
async def accept_winback(subscription_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    sub = await lifecycle_engine.accept_winback_offer(db, tenant_id, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@router.get("/members/{member_profile_id}/pause-count")
async def get_pause_count(member_profile_id: uuid.UUID, window_days: int = 365, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """A real count from PauseHistoryEntry, not a stand-in — this is
    the one piece of churn-risk context this service can answer for
    itself, from its own historical data."""
    count = await lifecycle_engine.count_pauses_in_window(db, tenant_id, member_profile_id, window_days)
    return {"member_profile_id": str(member_profile_id), "window_days": window_days, "pause_count": count}
