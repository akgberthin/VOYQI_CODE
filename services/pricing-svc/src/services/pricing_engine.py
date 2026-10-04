"""Pricing engine.

calculate_price() is a read-only preview — it never touches redemption
counts, so a member can see what a promo would do to their price without
that lookup itself consuming one of a limited number of redemptions.
Only redeem_promo() actually commits a redemption, and it does so with a
real per-user duplicate-redemption guard for the same order.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import PromoCode, PromoRedemption, RateCard


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RateCardNotFoundError(Exception):
    pass


class PromoCodeNotFoundError(Exception):
    pass


class PromoNotApplicableError(Exception):
    pass


class PromoExpiredError(Exception):
    pass


class PromoRedemptionLimitReachedError(Exception):
    pass


async def set_price(db: AsyncSession, *, tenant_id: str, mobility_class: str, market: str, price_cents: int, changed_by: str, reason: str | None = None) -> RateCard:
    if price_cents <= 0:
        raise ValueError("price_cents must be positive")

    now = utcnow()
    current = await get_current_rate_card(db, tenant_id=tenant_id, mobility_class=mobility_class, market=market, at=now, fall_back_to_national=False)
    if current is not None:
        current.effective_to = now

    new_card = RateCard(
        tenant_id=tenant_id, mobility_class=mobility_class, market=market, price_cents=price_cents,
        effective_from=now, effective_to=None, changed_by=changed_by, reason=reason,
    )
    db.add(new_card)
    await db.flush()
    return new_card


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip — normalize before comparing."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def get_current_rate_card(
    db: AsyncSession, *, tenant_id: str, mobility_class: str, market: str, at: datetime | None = None, fall_back_to_national: bool = True,
) -> RateCard | None:
    at = at or utcnow()
    result = await db.execute(
        select(RateCard).where(
            RateCard.tenant_id == tenant_id, RateCard.mobility_class == mobility_class, RateCard.market == market,
            RateCard.effective_from <= at, RateCard.effective_to.is_(None),
        )
    )
    card = result.scalar_one_or_none()
    if card is not None:
        return card

    if fall_back_to_national and market != "national":
        return await get_current_rate_card(db, tenant_id=tenant_id, mobility_class=mobility_class, market="national", at=at, fall_back_to_national=False)
    return None


async def get_price_history(db: AsyncSession, *, tenant_id: str, mobility_class: str, market: str) -> list[RateCard]:
    result = await db.execute(
        select(RateCard)
        .where(RateCard.tenant_id == tenant_id, RateCard.mobility_class == mobility_class, RateCard.market == market)
        .order_by(RateCard.effective_from.desc())
    )
    return list(result.scalars().all())


async def create_promo_code(
    db: AsyncSession, *, tenant_id: str, code: str, discount_type: str, discount_value: int,
    applicable_classes: str = "all", max_redemptions: int | None = None, max_redemptions_per_user: int = 1,
    valid_from: datetime | None = None, valid_until: datetime | None = None,
) -> PromoCode:
    promo = PromoCode(
        tenant_id=tenant_id, code=code.upper().strip(), discount_type=discount_type, discount_value=discount_value,
        applicable_classes=applicable_classes, max_redemptions=max_redemptions, max_redemptions_per_user=max_redemptions_per_user,
        valid_from=valid_from or utcnow(), valid_until=valid_until,
    )
    db.add(promo)
    await db.flush()
    return promo


async def _get_active_promo(db: AsyncSession, *, tenant_id: str, code: str, mobility_class: str) -> PromoCode:
    result = await db.execute(select(PromoCode).where(PromoCode.tenant_id == tenant_id, PromoCode.code == code.upper().strip()))
    promo = result.scalar_one_or_none()
    if promo is None or not promo.is_active:
        raise PromoCodeNotFoundError(f"no active promo code '{code}'")

    now = utcnow()
    if _aware(promo.valid_from) > now or (promo.valid_until is not None and _aware(promo.valid_until) < now):
        raise PromoExpiredError(f"promo code '{code}' is not currently valid")

    if promo.applicable_classes != "all" and mobility_class not in promo.applicable_classes.split(","):
        raise PromoNotApplicableError(f"promo code '{code}' does not apply to class '{mobility_class}'")

    if promo.max_redemptions is not None and promo.redemption_count >= promo.max_redemptions:
        raise PromoRedemptionLimitReachedError(f"promo code '{code}' has reached its total redemption limit")

    return promo


def _apply_discount(base_price_cents: int, promo: PromoCode) -> int:
    if promo.discount_type == "percentage":
        return min(base_price_cents, (base_price_cents * promo.discount_value) // 100)
    return min(base_price_cents, promo.discount_value)


async def calculate_price(db: AsyncSession, *, tenant_id: str, mobility_class: str, market: str = "national", promo_code: str | None = None) -> dict:
    """Read-only preview — never touches redemption counts."""
    rate_card = await get_current_rate_card(db, tenant_id=tenant_id, mobility_class=mobility_class, market=market)
    if rate_card is None:
        raise RateCardNotFoundError(f"no active rate card for class '{mobility_class}' in market '{market}'")

    discount_cents = 0
    if promo_code:
        promo = await _get_active_promo(db, tenant_id=tenant_id, code=promo_code, mobility_class=mobility_class)
        discount_cents = _apply_discount(rate_card.price_cents, promo)

    return {
        "mobility_class": mobility_class, "market": market, "currency": rate_card.currency,
        "base_price_cents": rate_card.price_cents, "discount_cents": discount_cents,
        "final_price_cents": rate_card.price_cents - discount_cents,
    }


async def redeem_promo(
    db: AsyncSession, *, tenant_id: str, code: str, user_id: uuid.UUID, mobility_class: str, market: str = "national", order_reference: str | None = None,
) -> PromoRedemption:
    promo = await _get_active_promo(db, tenant_id=tenant_id, code=code, mobility_class=mobility_class)

    # Duplicate-redemption guard for the same order (a retry-safety net,
    # the same principle as payment-svc's idempotency key, though the
    # stakes of a double-redemption here are lower than a double-charge).
    if order_reference:
        existing = await db.execute(
            select(PromoRedemption).where(PromoRedemption.promo_code_id == promo.id, PromoRedemption.user_id == user_id, PromoRedemption.order_reference == order_reference)
        )
        found = existing.scalar_one_or_none()
        if found is not None:
            return found

    user_redemptions = await db.execute(select(PromoRedemption).where(PromoRedemption.promo_code_id == promo.id, PromoRedemption.user_id == user_id))
    user_redemption_count = len(user_redemptions.scalars().all())
    if user_redemption_count >= promo.max_redemptions_per_user:
        raise PromoRedemptionLimitReachedError(f"user has already redeemed promo code '{code}' the maximum {promo.max_redemptions_per_user} time(s)")

    rate_card = await get_current_rate_card(db, tenant_id=tenant_id, mobility_class=mobility_class, market=market)
    if rate_card is None:
        raise RateCardNotFoundError(f"no active rate card for class '{mobility_class}' in market '{market}'")
    discount_cents = _apply_discount(rate_card.price_cents, promo)

    redemption = PromoRedemption(
        tenant_id=tenant_id, promo_code_id=promo.id, user_id=user_id, order_reference=order_reference, discount_applied_cents=discount_cents,
    )
    db.add(redemption)
    await db.execute(update(PromoCode).where(PromoCode.id == promo.id).values(redemption_count=PromoCode.redemption_count + 1))
    await db.flush()
    return redemption
