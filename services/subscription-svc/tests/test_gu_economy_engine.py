import pytest

from src.core.config import get_settings
from src.models import GUEarnTrigger
from src.services import gu_economy_engine, member_engine
from src.services.gu_economy_engine import InsufficientGUBalanceError

TENANT = "voyqi-holdings"


async def test_earn_increases_balance(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-1", "A", "a@example.com")
    entry = await gu_economy_engine.earn(db_session, TENANT, profile.id, GUEarnTrigger.LOW_MILEAGE, 100)
    assert entry.flagged_for_fraud is False

    await db_session.refresh(profile)
    assert profile.gu_balance == 100


async def test_fraud_flag_after_threshold_earns(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-2", "B", "b@example.com")
    threshold = get_settings().gu_fraud_earn_count_threshold
    last_entry = None
    for _ in range(threshold + 1):
        last_entry = await gu_economy_engine.earn(db_session, TENANT, profile.id, GUEarnTrigger.REFERRAL, 50)
    assert last_entry.flagged_for_fraud is True


async def test_redeem_reduces_balance(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-3", "C", "c@example.com")
    await gu_economy_engine.earn(db_session, TENANT, profile.id, GUEarnTrigger.ANNIVERSARY, 1000)
    await gu_economy_engine.create_catalog_item(db_session, TENANT, "priority_selection", "Priority Vehicle Selection", 500)

    entry = await gu_economy_engine.redeem(db_session, TENANT, profile.id, "priority_selection")
    assert entry.amount == -500

    await db_session.refresh(profile)
    assert profile.gu_balance == 500


async def test_redeem_insufficient_balance_raises(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-4", "D", "d@example.com")
    await gu_economy_engine.create_catalog_item(db_session, TENANT, "gift_subscription", "Gift Subscription", 5000)
    with pytest.raises(InsufficientGUBalanceError):
        await gu_economy_engine.redeem(db_session, TENANT, profile.id, "gift_subscription")


async def test_redeem_unknown_item_raises(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-5", "E", "e@example.com")
    with pytest.raises(ValueError):
        await gu_economy_engine.redeem(db_session, TENANT, profile.id, "nonexistent_item")


async def test_get_ledger_returns_all_entries(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-6", "F", "f@example.com")
    await gu_economy_engine.earn(db_session, TENANT, profile.id, GUEarnTrigger.LOW_MILEAGE, 100)
    await gu_economy_engine.earn(db_session, TENANT, profile.id, GUEarnTrigger.OFF_PEAK, 50)
    ledger = await gu_economy_engine.get_ledger(db_session, TENANT, profile.id)
    assert len(ledger) == 2
