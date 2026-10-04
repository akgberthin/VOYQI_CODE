import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.services import pricing_engine

TENANT = "voyqi-holdings"


@pytest.mark.asyncio
async def test_set_price_creates_a_rate_card(db_session):
    card = await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="executive", market="national", price_cents=85900, changed_by="admin")
    await db_session.commit()
    assert card.price_cents == 85900
    assert card.effective_to is None


@pytest.mark.asyncio
async def test_changing_price_closes_out_the_old_row_and_history_shows_both(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="executive", market="national", price_cents=85900, changed_by="admin", reason="launch price")
    await db_session.commit()
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="executive", market="national", price_cents=89900, changed_by="admin", reason="price increase")
    await db_session.commit()

    current = await pricing_engine.get_current_rate_card(db_session, tenant_id=TENANT, mobility_class="executive", market="national")
    assert current.price_cents == 89900
    assert current.effective_to is None

    history = await pricing_engine.get_price_history(db_session, tenant_id=TENANT, mobility_class="executive", market="national")
    assert len(history) == 2
    assert history[0].price_cents == 89900  # most recent first
    assert history[1].price_cents == 85900
    assert history[1].effective_to is not None  # the old one is closed out


@pytest.mark.asyncio
async def test_get_current_price_falls_back_to_national_when_no_market_specific_card(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await db_session.commit()

    card = await pricing_engine.get_current_rate_card(db_session, tenant_id=TENANT, mobility_class="urban", market="miami")
    assert card is not None
    assert card.market == "national"
    assert card.price_cents == 44900


@pytest.mark.asyncio
async def test_market_specific_price_overrides_the_national_fallback(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="miami", price_cents=49900, changed_by="admin", reason="high-demand market")
    await db_session.commit()

    card = await pricing_engine.get_current_rate_card(db_session, tenant_id=TENANT, mobility_class="urban", market="miami")
    assert card.market == "miami"
    assert card.price_cents == 49900


@pytest.mark.asyncio
async def test_calculate_price_without_promo(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="luxury", market="national", price_cents=149900, changed_by="admin")
    await db_session.commit()

    result = await pricing_engine.calculate_price(db_session, tenant_id=TENANT, mobility_class="luxury")
    assert result["base_price_cents"] == 149900
    assert result["discount_cents"] == 0
    assert result["final_price_cents"] == 149900


@pytest.mark.asyncio
async def test_calculate_price_with_percentage_promo(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="luxury", market="national", price_cents=100000, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="save10", discount_type="percentage", discount_value=10)
    await db_session.commit()

    result = await pricing_engine.calculate_price(db_session, tenant_id=TENANT, mobility_class="luxury", promo_code="SAVE10")
    assert result["discount_cents"] == 10000
    assert result["final_price_cents"] == 90000


@pytest.mark.asyncio
async def test_calculate_price_with_fixed_amount_promo(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="FLAT50", discount_type="fixed_amount", discount_value=5000)
    await db_session.commit()

    result = await pricing_engine.calculate_price(db_session, tenant_id=TENANT, mobility_class="urban", promo_code="FLAT50")
    assert result["discount_cents"] == 5000
    assert result["final_price_cents"] == 39900


@pytest.mark.asyncio
async def test_fixed_amount_discount_never_exceeds_base_price(db_session):
    """A $50 flat discount on a $10 item should discount at most $10 —
    never produce a negative final price."""
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=1000, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="BIGDISCOUNT", discount_type="fixed_amount", discount_value=5000)
    await db_session.commit()

    result = await pricing_engine.calculate_price(db_session, tenant_id=TENANT, mobility_class="urban", promo_code="BIGDISCOUNT")
    assert result["final_price_cents"] == 0
    assert result["discount_cents"] == 1000


@pytest.mark.asyncio
async def test_calculate_price_is_read_only_and_does_not_consume_a_redemption(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    promo = await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="PREVIEW", discount_type="percentage", discount_value=10, max_redemptions=1)
    await db_session.commit()
    promo_id = promo.id

    for _ in range(5):
        await pricing_engine.calculate_price(db_session, tenant_id=TENANT, mobility_class="urban", promo_code="PREVIEW")

    from sqlalchemy import select

    from src.models import PromoCode
    result = await db_session.execute(select(PromoCode).where(PromoCode.id == promo_id))
    assert result.scalar_one().redemption_count == 0


@pytest.mark.asyncio
async def test_redeem_promo_increments_redemption_count(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="REAL10", discount_type="percentage", discount_value=10)
    await db_session.commit()

    await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="REAL10", user_id=uuid.uuid4(), mobility_class="urban")
    await db_session.commit()

    from sqlalchemy import select

    from src.models import PromoCode
    result = await db_session.execute(select(PromoCode).where(PromoCode.tenant_id == TENANT, PromoCode.code == "REAL10"))
    assert result.scalar_one().redemption_count == 1


@pytest.mark.asyncio
async def test_redeem_promo_respects_per_user_limit(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="ONCE", discount_type="percentage", discount_value=10, max_redemptions_per_user=1)
    await db_session.commit()
    user_id = uuid.uuid4()

    await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="ONCE", user_id=user_id, mobility_class="urban", order_reference="order-1")
    await db_session.commit()

    with pytest.raises(pricing_engine.PromoRedemptionLimitReachedError):
        await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="ONCE", user_id=user_id, mobility_class="urban", order_reference="order-2")


@pytest.mark.asyncio
async def test_redeem_promo_respects_total_redemption_limit_across_users(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="LIMITED", discount_type="percentage", discount_value=10, max_redemptions=1, max_redemptions_per_user=5)
    await db_session.commit()

    await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="LIMITED", user_id=uuid.uuid4(), mobility_class="urban")
    await db_session.commit()

    with pytest.raises(pricing_engine.PromoRedemptionLimitReachedError):
        await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="LIMITED", user_id=uuid.uuid4(), mobility_class="urban")


@pytest.mark.asyncio
async def test_redeem_promo_with_same_order_reference_is_idempotent(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="RETRY", discount_type="percentage", discount_value=10)
    await db_session.commit()
    user_id = uuid.uuid4()

    first = await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="RETRY", user_id=user_id, mobility_class="urban", order_reference="order-abc")
    await db_session.commit()
    second = await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="RETRY", user_id=user_id, mobility_class="urban", order_reference="order-abc")
    await db_session.commit()

    assert first.id == second.id

    from sqlalchemy import select

    from src.models import PromoCode
    result = await db_session.execute(select(PromoCode).where(PromoCode.tenant_id == TENANT, PromoCode.code == "RETRY"))
    assert result.scalar_one().redemption_count == 1  # not double-counted


@pytest.mark.asyncio
async def test_redeem_promo_not_applicable_to_class_raises(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(db_session, tenant_id=TENANT, code="LUXURYONLY", discount_type="percentage", discount_value=10, applicable_classes="luxury,performance")
    await db_session.commit()

    with pytest.raises(pricing_engine.PromoNotApplicableError):
        await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="LUXURYONLY", user_id=uuid.uuid4(), mobility_class="urban")


@pytest.mark.asyncio
async def test_expired_promo_raises(db_session):
    await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=44900, changed_by="admin")
    await pricing_engine.create_promo_code(
        db_session, tenant_id=TENANT, code="EXPIRED", discount_type="percentage", discount_value=10,
        valid_from=datetime.now(timezone.utc) - timedelta(days=30), valid_until=datetime.now(timezone.utc) - timedelta(days=1),
    )
    await db_session.commit()

    with pytest.raises(pricing_engine.PromoExpiredError):
        await pricing_engine.redeem_promo(db_session, tenant_id=TENANT, code="EXPIRED", user_id=uuid.uuid4(), mobility_class="urban")


@pytest.mark.asyncio
async def test_calculate_price_unknown_class_raises_rate_card_not_found(db_session):
    with pytest.raises(pricing_engine.RateCardNotFoundError):
        await pricing_engine.calculate_price(db_session, tenant_id=TENANT, mobility_class="nonexistent_class")


@pytest.mark.asyncio
async def test_set_price_rejects_non_positive_price(db_session):
    with pytest.raises(ValueError):
        await pricing_engine.set_price(db_session, tenant_id=TENANT, mobility_class="urban", market="national", price_cents=0, changed_by="admin")
