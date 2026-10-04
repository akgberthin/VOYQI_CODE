import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import GUEarnTrigger, GULedgerEntryType
from src.services import gu_economy_engine
from src.services.gu_economy_engine import InsufficientGUBalanceError

router = APIRouter(prefix="/api/v1/subscription", tags=["gu-economy"])


class EarnRequest(BaseModel):
    member_profile_id: uuid.UUID
    trigger: GUEarnTrigger
    amount: int


class RedeemRequest(BaseModel):
    member_profile_id: uuid.UUID
    catalog_item_key: str


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_type: GULedgerEntryType
    amount: int
    earn_trigger: GUEarnTrigger | None
    redemption_item: str
    flagged_for_fraud: bool


class CatalogItemCreate(BaseModel):
    key: str
    name: str
    gu_cost: int


@router.post("/gu/earn", response_model=LedgerEntryOut, status_code=201)
async def earn(
    body: EarnRequest,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    entry = await gu_economy_engine.earn(db, tenant_id=tenant_id, member_profile_id=body.member_profile_id, trigger=body.trigger, amount=body.amount)
    if entry.flagged_for_fraud:
        background_tasks.add_task(
            publish_event,
            "subscription.gu_economy",
            "gu.fraud_suspected",
            tenant_id,
            {
                "ledger_entry_id": str(entry.id), "member_profile_id": str(body.member_profile_id),
                "trigger": body.trigger.value, "amount": body.amount,
            },
        )
    return entry


@router.post("/gu/redeem", response_model=LedgerEntryOut, status_code=201)
async def redeem(body: RedeemRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await gu_economy_engine.redeem(db, tenant_id=tenant_id, member_profile_id=body.member_profile_id, catalog_item_key=body.catalog_item_key)
    except InsufficientGUBalanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/gu/catalog-items", status_code=201)
async def create_catalog_item(body: CatalogItemCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    item = await gu_economy_engine.create_catalog_item(db, tenant_id, body.key, body.name, body.gu_cost)
    return {"id": str(item.id), "key": item.key}


@router.get("/members/{member_profile_id}/gu-ledger", response_model=list[LedgerEntryOut])
async def get_ledger(member_profile_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await gu_economy_engine.get_ledger(db, tenant_id, member_profile_id)
