import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import ChecklistStatus, TouchpointEventType, TouchpointSource, TouchpointStatus
from src.services import hub_engine, ops_board_engine
from src.services.ops_board_engine import ChecklistIncompleteError

router = APIRouter(prefix="/api/v1/hub", tags=["ops-board"])


# ── Schemas ───────────────────────────────────────────────────────────

class TouchpointCreate(BaseModel):
    hub_id: uuid.UUID
    event_type: TouchpointEventType
    source: TouchpointSource
    member_or_renter_ref: str
    vehicle_id: uuid.UUID | None = None


class TouchpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_id: uuid.UUID
    event_type: TouchpointEventType
    source: TouchpointSource
    member_or_renter_ref: str
    vehicle_id: uuid.UUID | None
    scheduled_at: datetime
    completed_at: datetime | None
    status: TouchpointStatus
    exception_reason: str


class ExceptionFlag(BaseModel):
    reason: str


class ChecklistOpen(BaseModel):
    hub_id: uuid.UUID
    vehicle_id: uuid.UUID
    item_labels: list[str]
    touchpoint_event_id: uuid.UUID | None = None


class ChecklistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checklist_id: uuid.UUID
    label: str
    is_completed: bool


class ChecklistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_id: uuid.UUID
    vehicle_id: uuid.UUID
    status: ChecklistStatus
    completed_at: datetime | None


class CardIssue(BaseModel):
    member_profile_id: str
    card_number: str


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: str
    card_number: str
    status: str
    issued_at: datetime


class TapCreate(BaseModel):
    hub_id: uuid.UUID
    card_id: uuid.UUID


class TapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_id: uuid.UUID
    card_id: uuid.UUID
    tapped_at: datetime
    anomaly_flagged: bool
    anomaly_reason: str


# ── Touchpoints ───────────────────────────────────────────────────────

@router.post("/touchpoints", response_model=TouchpointOut, status_code=201)
async def schedule_touchpoint(body: TouchpointCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ops_board_engine.schedule_touchpoint(
        db, tenant_id, body.hub_id, body.event_type, body.source, body.member_or_renter_ref, body.vehicle_id
    )


@router.get("/hubs/{hub_id}/touchpoints", response_model=list[TouchpointOut])
async def list_touchpoints(hub_id: uuid.UUID, status: TouchpointStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ops_board_engine.list_touchpoints_for_hub(db, tenant_id, hub_id, status=status)


@router.post("/touchpoints/{event_id}/complete", response_model=TouchpointOut)
async def complete_touchpoint(event_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    event = await ops_board_engine.complete_touchpoint(db, tenant_id, event_id)
    if event is None:
        raise HTTPException(status_code=409, detail="Touchpoint not found or not scheduled")
    return event


@router.post("/touchpoints/{event_id}/flag-exception", response_model=TouchpointOut)
async def flag_exception(
    event_id: uuid.UUID, body: ExceptionFlag, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    event = await ops_board_engine.flag_exception(db, tenant_id, event_id, body.reason)
    if event is None:
        raise HTTPException(status_code=404, detail="Touchpoint not found")

    background_tasks.add_task(
        publish_event,
        "hub.operations",
        "hub.touchpoint_exception_flagged",
        tenant_id,
        {
            "touchpoint_id": str(event.id),
            "hub_id": str(event.hub_id),
            "event_type": event.event_type.value,
            "reason": body.reason,
        },
    )
    return event


# ── Prep checklists ───────────────────────────────────────────────────

@router.post("/prep-checklists", response_model=ChecklistOut, status_code=201)
async def open_checklist(body: ChecklistOpen, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ops_board_engine.open_checklist(
        db, tenant_id, body.hub_id, body.vehicle_id, body.item_labels, touchpoint_event_id=body.touchpoint_event_id
    )


@router.get("/prep-checklists/{checklist_id}/items", response_model=list[ChecklistItemOut])
async def list_checklist_items(checklist_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await ops_board_engine.list_checklist_items(db, tenant_id, checklist_id)


@router.post("/prep-checklist-items/{item_id}/check", response_model=ChecklistItemOut)
async def check_item(item_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    item = await ops_board_engine.check_item(db, tenant_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    return item


@router.post("/prep-checklists/{checklist_id}/mark-ready", response_model=ChecklistOut)
async def mark_checklist_ready(checklist_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await ops_board_engine.mark_checklist_ready(db, tenant_id, checklist_id)
    except ChecklistIncompleteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── VOYQI Card + taps ─────────────────────────────────────────────────

@router.post("/cards", response_model=CardOut, status_code=201)
async def issue_card(body: CardIssue, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    card = await ops_board_engine.issue_card(db, tenant_id, body.member_profile_id, body.card_number)
    return {**card.__dict__, "status": card.status.value}


@router.post("/cards/{card_id}/reactivate", response_model=CardOut)
async def reactivate_card(card_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    card = await ops_board_engine.reactivate_card(db, tenant_id, card_id)
    if card is None:
        raise HTTPException(status_code=409, detail="Card not found or not suspended")
    return {**card.__dict__, "status": card.status.value}


@router.post("/card-taps", response_model=TapOut, status_code=201)
async def record_card_tap(
    body: TapCreate, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    try:
        tap = await ops_board_engine.record_card_tap(db, tenant_id, body.hub_id, body.card_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if tap.anomaly_flagged:
        background_tasks.add_task(
            publish_event,
            "hub.security",
            "card.geographic_anomaly_detected",
            tenant_id,
            {
                "tap_id": str(tap.id),
                "card_id": str(body.card_id),
                "hub_id": str(body.hub_id),
                "reason": tap.anomaly_reason,
            },
        )
    return tap


@router.get("/cards/{card_id}/tap-history")
async def get_tap_history(card_id: uuid.UUID, limit: int = 20, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    taps = await ops_board_engine.list_taps_for_card(db, tenant_id, card_id, limit=limit)
    enriched = []
    for tap in taps:
        hub = await hub_engine.get_hub(db, tenant_id, tap.hub_id)
        enriched.append({
            "tap_id": str(tap.id),
            "hub_id": str(tap.hub_id),
            "hub_name": hub.name if hub else "(unknown hub)",
            "tapped_at": tap.tapped_at.isoformat(),
            "anomaly_flagged": tap.anomaly_flagged,
        })
    return enriched
