import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import pricing_engine

router = APIRouter()


class SetPriceIn(BaseModel):
    mobility_class: str
    market: str = "national"
    price_cents: int
    changed_by: str
    reason: str | None = None


class CreatePromoIn(BaseModel):
    code: str
    discount_type: str
    discount_value: int
    applicable_classes: str = "all"
    max_redemptions: int | None = None
    max_redemptions_per_user: int = 1
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class RedeemPromoIn(BaseModel):
    code: str
    user_id: uuid.UUID
    mobility_class: str
    market: str = "national"
    order_reference: str | None = None


PROMO_ERRORS = (
    pricing_engine.PromoCodeNotFoundError, pricing_engine.PromoNotApplicableError,
    pricing_engine.PromoExpiredError, pricing_engine.PromoRedemptionLimitReachedError,
)


@router.post("/rate-cards", status_code=201)
async def set_price(payload: SetPriceIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        card = await pricing_engine.set_price(db, tenant_id=tenant_id, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    background_tasks.add_task(
        publish_event, "pricing.rate_cards", "pricing.price_set", tenant_id,
        {"rate_card_id": str(card.id), "mobility_class": card.mobility_class, "market": card.market, "price_cents": card.price_cents, "effective_from": str(card.effective_from)},
    )
    return {"id": card.id, "mobility_class": card.mobility_class, "market": card.market, "price_cents": card.price_cents, "effective_from": card.effective_from}


@router.get("/rate-cards/current")
async def get_current_price(mobility_class: str, market: str = "national", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    card = await pricing_engine.get_current_rate_card(db, tenant_id=tenant_id, mobility_class=mobility_class, market=market)
    if card is None:
        raise HTTPException(status_code=404, detail=f"no active rate card for class '{mobility_class}' in market '{market}'")
    return {"id": card.id, "mobility_class": card.mobility_class, "market": card.market, "price_cents": card.price_cents, "currency": card.currency, "effective_from": card.effective_from}


@router.get("/rate-cards/history")
async def get_price_history(mobility_class: str, market: str = "national", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    cards = await pricing_engine.get_price_history(db, tenant_id=tenant_id, mobility_class=mobility_class, market=market)
    return [{"id": c.id, "price_cents": c.price_cents, "effective_from": c.effective_from, "effective_to": c.effective_to, "changed_by": c.changed_by, "reason": c.reason} for c in cards]


@router.get("/calculate")
async def calculate_price(mobility_class: str, market: str = "national", promo_code: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await pricing_engine.calculate_price(db, tenant_id=tenant_id, mobility_class=mobility_class, market=market, promo_code=promo_code)
    except pricing_engine.RateCardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PROMO_ERRORS as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/promo-codes", status_code=201)
async def create_promo_code(payload: CreatePromoIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    promo = await pricing_engine.create_promo_code(db, tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    background_tasks.add_task(
        publish_event, "pricing.promotions", "pricing.promo_created", tenant_id,
        {"promo_code_id": str(promo.id), "code": promo.code, "discount_type": promo.discount_type, "discount_value": promo.discount_value},
    )
    return {"id": promo.id, "code": promo.code, "discount_type": promo.discount_type, "discount_value": promo.discount_value}


@router.post("/promo-codes/redeem", status_code=201)
async def redeem_promo(payload: RedeemPromoIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        redemption = await pricing_engine.redeem_promo(db, tenant_id=tenant_id, **payload.model_dump())
    except pricing_engine.RateCardNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PROMO_ERRORS as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    background_tasks.add_task(
        publish_event, "pricing.promotions", "pricing.promo_redeemed", tenant_id,
        {"redemption_id": str(redemption.id), "code": payload.code, "discount_applied_cents": redemption.discount_applied_cents},
    )
    return {"id": redemption.id, "discount_applied_cents": redemption.discount_applied_cents}
