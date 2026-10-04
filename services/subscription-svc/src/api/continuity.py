import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import MobilityClass
from src.services import continuity_engine

router = APIRouter(prefix="/api/v1/subscription", tags=["continuity"])


class TravelDeclare(BaseModel):
    member_profile_id: uuid.UUID
    source_class: MobilityClass
    destination_market: str
    destination_hub_id: str


class DeclarationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_profile_id: uuid.UUID
    destination_market: str
    resolved_equivalent_class: MobilityClass | None
    staged_vehicle_id: uuid.UUID | None
    staged_hub_id: uuid.UUID | None
    is_active: bool
    created_at: datetime


@router.post("/travel-declarations", response_model=DeclarationOut, status_code=201)
async def declare_travel(
    body: TravelDeclare, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    declaration = await continuity_engine.declare_travel(
        db, tenant_id, body.member_profile_id, body.source_class, body.destination_market, body.destination_hub_id
    )
    background_tasks.add_task(
        publish_event,
        "subscription.continuity",
        "travel.activated",
        tenant_id,
        {
            "declaration_id": str(declaration.id),
            "member_profile_id": str(body.member_profile_id),
            "destination_hub_id": body.destination_hub_id,
            "destination_market": body.destination_market,
            "source_class": body.source_class.value,
        },
    )
    return declaration


@router.post("/travel-declarations/{declaration_id}/complete", response_model=DeclarationOut)
async def complete_travel(declaration_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    declaration = await continuity_engine.complete_travel(db, tenant_id, declaration_id)
    if declaration is None:
        raise HTTPException(status_code=409, detail="Declaration not found or already completed")
    return declaration


@router.get("/members/{member_profile_id}/travel-declarations", response_model=list[DeclarationOut])
async def list_declarations(member_profile_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await continuity_engine.list_active_declarations(db, tenant_id, member_profile_id)
