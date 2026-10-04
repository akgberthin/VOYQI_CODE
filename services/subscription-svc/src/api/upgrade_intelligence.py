import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import MobilityClass, UpgradeCaseStatus
from src.services import upgrade_intelligence_engine
from src.services.upgrade_intelligence_engine import (
    NoPlanForClassError,
    UpgradeCaseNotOfferedError,
)

router = APIRouter(prefix="/api/v1/subscription", tags=["upgrade-intelligence"])


class AboveClassRequestCreate(BaseModel):
    member_profile_id: uuid.UUID
    subscription_id: uuid.UUID
    subscribed_class: MobilityClass
    requested_class: MobilityClass


@router.post("/above-class-requests", status_code=202)
async def record_above_class_request(body: AboveClassRequestCreate, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Every above-class request is a real signal worth logging even
    before a pattern forms. Also synchronously asks the Member
    Experience Agent to check whether this one pushed the member over
    the pattern threshold — mirrors how initiate_cancellation calls
    the winback evaluation, never blocking on a slow or unreachable
    agent."""
    _record, evaluation = await upgrade_intelligence_engine.record_above_class_request(
        db, tenant_id, body.member_profile_id, body.subscription_id, body.subscribed_class, body.requested_class
    )
    background_tasks.add_task(
        publish_event, "subscription.upgrade", "subscription.above_class_request_recorded", tenant_id,
        {"member_profile_id": str(body.member_profile_id), "subscription_id": str(body.subscription_id), "subscribed_class": body.subscribed_class.value, "requested_class": body.requested_class.value},
    )
    return {"status": "recorded", "upgrade_evaluation": evaluation}


@router.get("/members/{member_profile_id}/upgrade-pattern")
async def get_upgrade_pattern(member_profile_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The agent's own lookup — mirrors HRM's pattern of every AI-first
    capability fetching its own source data rather than trusting
    whatever a caller happened to pass in the event payload."""
    top_class, count, reasoning = await upgrade_intelligence_engine.detect_upgrade_pattern(db, tenant_id, member_profile_id)
    return {"member_profile_id": str(member_profile_id), "pattern_detected": top_class is not None, "recommended_class": top_class.value if top_class else None, "request_count": count, "reasoning": reasoning}


class UpgradeCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    subscription_id: uuid.UUID
    current_class: MobilityClass
    recommended_class: MobilityClass
    above_class_request_count: int
    reasoning: str
    status: UpgradeCaseStatus
    created_at: datetime
    resolved_at: datetime | None


class UpgradeCaseCreate(BaseModel):
    member_profile_id: uuid.UUID
    subscription_id: uuid.UUID
    current_class: MobilityClass
    recommended_class: MobilityClass
    above_class_request_count: int
    reasoning: str


@router.post("/upgrade-cases", response_model=UpgradeCaseOut, status_code=201)
async def create_upgrade_case(body: UpgradeCaseCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """No manual creation path in any UI by convention — this is the
    agent's endpoint. A case always starts OFFERED; nothing here
    touches billing."""
    return await upgrade_intelligence_engine.create_upgrade_case(
        db, tenant_id, body.member_profile_id, body.subscription_id, body.current_class, body.recommended_class, body.above_class_request_count, body.reasoning,
    )


@router.get("/upgrade-cases", response_model=list[UpgradeCaseOut])
async def list_upgrade_cases(member_profile_id: uuid.UUID | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await upgrade_intelligence_engine.list_upgrade_cases(db, tenant_id, member_profile_id)


@router.post("/upgrade-cases/{case_id}/accept")
async def accept_upgrade_case(case_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The member's own acceptance — the one action in this module
    that changes billing, and the one action the agent that offered
    the case never calls on its own."""
    try:
        case, subscription = await upgrade_intelligence_engine.accept_upgrade_case(db, tenant_id, case_id)
    except UpgradeCaseNotOfferedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoPlanForClassError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(
        publish_event, "subscription.upgrade", "subscription.upgrade_accepted", tenant_id,
        {"case_id": str(case_id), "subscription_id": str(subscription.id), "new_class": case.recommended_class.value},
    )
    return {"case_id": str(case.id), "status": case.status.value, "subscription_id": str(subscription.id), "new_plan_id": str(subscription.plan_id)}


@router.post("/upgrade-cases/{case_id}/decline", response_model=UpgradeCaseOut)
async def decline_upgrade_case(case_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await upgrade_intelligence_engine.decline_upgrade_case(db, tenant_id, case_id)
    except UpgradeCaseNotOfferedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/members/{member_profile_id}/request-upgrade-evaluation", status_code=202)
async def request_upgrade_evaluation(member_profile_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Explicit on-demand trigger for the same pattern check the
    above-class-request recorder already fires automatically —
    useful for an ops-initiated re-check."""
    background_tasks.add_task(
        publish_event, "subscription.upgrade", "subscription.upgrade_evaluation_requested", tenant_id,
        {"member_profile_id": str(member_profile_id)},
    )
    return {"status": "upgrade_evaluation_requested"}
