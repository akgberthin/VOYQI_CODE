import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import fi_engine

router = APIRouter(prefix="/api/v1/dms/fi", tags=["fi-compliance"])


class AdverseActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    deal_id: uuid.UUID
    reason: str
    status: str


class AdverseActionCreate(BaseModel):
    deal_id: uuid.UUID
    reason: str


@router.post("/adverse-action-notices", response_model=AdverseActionOut, status_code=201)
async def issue_adverse_action(body: AdverseActionCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Fully autonomous — an ECOA/Reg B legal deadline, not a
    judgment call. Issued immediately, no 'issue now' step."""
    notice = await fi_engine.auto_issue_adverse_action(db, tenant_id, body.deal_id, body.reason)
    return notice


class RedFlagCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    deal_id: uuid.UUID
    flags: list
    overridden_by: str | None
    override_reason: str | None


class RedFlagCheckCreate(BaseModel):
    deal_id: uuid.UUID
    flags: list[str] = []


@router.post("/red-flag-checks", response_model=RedFlagCheckOut, status_code=201)
async def create_red_flag_check(body: RedFlagCheckCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await fi_engine.create_red_flag_check(db, tenant_id, body.deal_id, body.flags)


class RedFlagOverrideRequest(BaseModel):
    overridden_by: str
    reason: str


@router.post("/red-flag-checks/{check_id}/override", response_model=RedFlagCheckOut)
async def override_red_flag(check_id: uuid.UUID, body: RedFlagOverrideRequest, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The ONE endpoint on this entire platform that structurally
    refuses to proceed without a human-typed reason — the FTC Red
    Flags Rule exists to prevent identity theft, a security matter,
    not a business judgment call. No default, no bypass, no AI path."""
    try:
        check = await fi_engine.override_red_flag(db, tenant_id, check_id, body.overridden_by, body.reason)
    except fi_engine.MissingOverrideReasonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    background_tasks.add_task(
        publish_event, "dms.fi", "red_flag.check_overridden", tenant_id,
        {"check_id": str(check.id), "deal_id": str(check.deal_id), "flags_detected": list(check.flags), "overridden_by": body.overridden_by, "override_reason": body.reason},
    )
    return check
