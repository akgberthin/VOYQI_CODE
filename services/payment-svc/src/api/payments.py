import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.models import Charge
from src.services import payment_engine

router = APIRouter()


class AddPaymentMethodIn(BaseModel):
    user_id: uuid.UUID
    provider_token: str
    method_type: str
    last4: str
    brand: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    is_default: bool = False


class ChargeIn(BaseModel):
    user_id: uuid.UUID
    payment_method_id: uuid.UUID
    amount_cents: int
    currency: str = "usd"
    description: str | None = None


class RefundIn(BaseModel):
    amount_cents: int | None = None
    reason: str | None = None


class WebhookIn(BaseModel):
    provider: str
    provider_event_id: str
    event_type: str
    payload: dict


def _serialize_charge(c) -> dict:
    return {
        "id": c.id, "status": c.status, "amount_cents": c.amount_cents, "currency": c.currency,
        "provider_mode": c.provider_mode, "provider_charge_id": c.provider_charge_id,
        "failure_reason": c.failure_reason, "refunded_amount_cents": c.refunded_amount_cents,
    }


@router.post("/payment-methods", status_code=201)
async def add_payment_method(payload: AddPaymentMethodIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    method = await payment_engine.add_payment_method(db, tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    background_tasks.add_task(
        publish_event, "payment.methods", "payment.method_added", tenant_id,
        {"payment_method_id": str(method.id), "user_id": str(method.user_id), "brand": method.brand, "last4": method.last4},
    )
    return {"id": method.id, "last4": method.last4, "brand": method.brand, "is_default": method.is_default, "provider_mode": method.provider_mode}


@router.post("/charges", status_code=201)
async def create_charge(
    payload: ChargeIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for all charge requests")
    # An idempotent retry returns the original charge — it is not a new
    # payment, so it must not publish a second payment event.
    replay = (await db.execute(select(Charge.id).where(Charge.tenant_id == tenant_id, Charge.idempotency_key == idempotency_key))).first() is not None
    try:
        result = await payment_engine.charge(db, tenant_id=tenant_id, idempotency_key=idempotency_key, **payload.model_dump())
    except payment_engine.PaymentMethodNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    if not replay:
        background_tasks.add_task(
            publish_event, "payment.charges", "payment.processed" if result.status == "succeeded" else "payment.failed", tenant_id,
            {
                "charge_id": str(result.id), "user_id": str(result.user_id), "amount_cents": result.amount_cents,
                "currency": result.currency, "status": result.status, "failure_reason": result.failure_reason,
            },
        )
    return _serialize_charge(result)


@router.get("/charges/{charge_id}")
async def get_charge(charge_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await payment_engine.get_charge(db, tenant_id=tenant_id, charge_id=charge_id)
    except payment_engine.ChargeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_charge(result)


@router.post("/charges/{charge_id}/refund", status_code=201)
async def refund_charge(charge_id: uuid.UUID, payload: RefundIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        result = await payment_engine.refund(db, tenant_id=tenant_id, charge_id=charge_id, amount_cents=payload.amount_cents, reason=payload.reason)
    except payment_engine.ChargeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except payment_engine.ChargeNotRefundableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except payment_engine.RefundAmountExceedsChargeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    background_tasks.add_task(
        publish_event, "payment.refunds", "payment.refunded", tenant_id,
        {"refund_id": str(result.id), "charge_id": str(charge_id), "amount_cents": result.amount_cents, "status": result.status, "reason": payload.reason},
    )
    return {"id": result.id, "status": result.status, "amount_cents": result.amount_cents, "failure_reason": result.failure_reason}


@router.post("/webhooks", status_code=200)
async def receive_webhook(payload: WebhookIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    event = await payment_engine.process_webhook(
        db, tenant_id=tenant_id, provider=payload.provider, provider_event_id=payload.provider_event_id,
        event_type=payload.event_type, payload=payload.payload,
    )
    await db.commit()
    if event is None:
        return {"status": "already_processed"}
    return {"status": "processed", "id": event.id}
