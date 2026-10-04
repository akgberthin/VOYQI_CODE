import uuid
from datetime import datetime, timedelta, timezone

from src.models import SubscriptionStatus
from src.services import (
    ai_orchestration_tools,
    lifecycle_engine,
    member_engine,
    plan_engine,
)

TENANT = "voyqi-holdings"


async def test_create_subscription(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    assert sub.status == SubscriptionStatus.ACTIVE


async def test_create_subscription_syncs_member_mobility_class(db_session):
    """Regression: creating a subscription must sync the member's own
    mobility_class to match the plan they just subscribed to — a
    member profile whose class doesn't match their active
    subscription is a real data inconsistency, not a display nuance."""
    from src.models import MobilityClass
    member = await member_engine.create_member_profile(db_session, TENANT, "user-sync-1", "Sync Test", "sync@example.com")
    plan = await plan_engine.create_plan(db_session, tenant_id=TENANT, mobility_class=MobilityClass.URBAN, market="miami", monthly_price_usd=449.0, entitlements={})
    assert member.mobility_class is None

    await lifecycle_engine.create_subscription(db_session, TENANT, member.id, plan.id)

    refreshed = await member_engine.get_member_profile(db_session, TENANT, member.id)
    assert refreshed.mobility_class == MobilityClass.URBAN


async def test_pause_and_resume(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    resume_at = datetime.now(timezone.utc) + timedelta(days=30)
    paused = await lifecycle_engine.pause_subscription(db_session, TENANT, sub.id, resume_at)
    assert paused.status == SubscriptionStatus.PAUSED

    resumed = await lifecycle_engine.resume_subscription(db_session, TENANT, sub.id)
    assert resumed.status == SubscriptionStatus.ACTIVE
    assert resumed.resume_at is None


async def test_cannot_pause_already_paused(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    resume_at = datetime.now(timezone.utc) + timedelta(days=30)
    await lifecycle_engine.pause_subscription(db_session, TENANT, sub.id, resume_at)
    result = await lifecycle_engine.pause_subscription(db_session, TENANT, sub.id, resume_at)
    assert result is None


async def test_cannot_resume_active_subscription(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    result = await lifecycle_engine.resume_subscription(db_session, TENANT, sub.id)
    assert result is None


async def test_initiate_cancellation_returns_offer_when_agent_reachable(db_session, monkeypatch):
    monkeypatch.setattr(
        ai_orchestration_tools, "request_winback_evaluation",
        lambda tenant_id, member_profile_id, subscription_id, reason: {"offer": "3 months at 20% off", "gu_bonus": 500},
    )
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    result = await lifecycle_engine.initiate_cancellation(db_session, TENANT, sub.id, reason="too expensive")
    assert result["found"] is True
    assert result["winback_offer"]["offer"] == "3 months at 20% off"


async def test_initiate_cancellation_proceeds_when_agent_unreachable(db_session, monkeypatch):
    monkeypatch.setattr(
        ai_orchestration_tools, "request_winback_evaluation",
        lambda tenant_id, member_profile_id, subscription_id, reason: None,
    )
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    result = await lifecycle_engine.initiate_cancellation(db_session, TENANT, sub.id, reason="moving abroad")
    assert result["found"] is True
    assert result["winback_offer"] is None

    # cancellation can still proceed even with no offer / agent unreachable
    finalized = await lifecycle_engine.finalize_cancellation(db_session, TENANT, sub.id, reason="moving abroad")
    assert finalized.status == SubscriptionStatus.CANCELLED


async def test_initiate_cancellation_unknown_subscription(db_session):
    result = await lifecycle_engine.initiate_cancellation(db_session, TENANT, uuid.uuid4(), reason="x")
    assert result["found"] is False


async def test_finalize_cancellation_records_exit_survey(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    finalized = await lifecycle_engine.finalize_cancellation(
        db_session, TENANT, sub.id, reason="switching to competitor", exit_survey={"satisfaction": 2, "would_recommend": False},
    )
    assert finalized.cancellation_reason == "switching to competitor"
    assert finalized.exit_survey["satisfaction"] == 2


async def test_cannot_finalize_cancellation_twice(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    await lifecycle_engine.finalize_cancellation(db_session, TENANT, sub.id, reason="x")
    second = await lifecycle_engine.finalize_cancellation(db_session, TENANT, sub.id, reason="y")
    assert second is None


async def test_accept_winback_offer(db_session):
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, uuid.uuid4(), uuid.uuid4())
    accepted = await lifecycle_engine.accept_winback_offer(db_session, TENANT, sub.id)
    assert accepted.win_back_offer_extended is True
    assert accepted.status == SubscriptionStatus.ACTIVE  # accepting keeps the subscription alive


async def test_pausing_creates_a_real_history_entry(db_session):
    member_id = uuid.uuid4()
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, member_id, uuid.uuid4())
    resume_at = datetime.now(timezone.utc) + timedelta(days=30)
    await lifecycle_engine.pause_subscription(db_session, TENANT, sub.id, resume_at)

    count = await lifecycle_engine.count_pauses_in_window(db_session, TENANT, member_id)
    assert count == 1


async def test_resuming_closes_the_open_history_entry(db_session):
    from sqlalchemy import select

    from src.models import PauseHistoryEntry

    member_id = uuid.uuid4()
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, member_id, uuid.uuid4())
    resume_at = datetime.now(timezone.utc) + timedelta(days=30)
    await lifecycle_engine.pause_subscription(db_session, TENANT, sub.id, resume_at)
    await lifecycle_engine.resume_subscription(db_session, TENANT, sub.id)

    result = await db_session.execute(select(PauseHistoryEntry).where(PauseHistoryEntry.subscription_id == sub.id))
    entry = result.scalar_one()
    assert entry.resumed_at is not None


async def test_multiple_pause_cycles_all_count_toward_the_window(db_session):
    member_id = uuid.uuid4()
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, member_id, uuid.uuid4())
    resume_at = datetime.now(timezone.utc) + timedelta(days=30)

    for _ in range(3):
        await lifecycle_engine.pause_subscription(db_session, TENANT, sub.id, resume_at)
        await lifecycle_engine.resume_subscription(db_session, TENANT, sub.id)

    count = await lifecycle_engine.count_pauses_in_window(db_session, TENANT, member_id)
    assert count == 3


async def test_pauses_outside_the_window_are_not_counted(db_session):
    from src.models import PauseHistoryEntry

    member_id = uuid.uuid4()
    sub = await lifecycle_engine.create_subscription(db_session, TENANT, member_id, uuid.uuid4())
    # An old pause well outside a 365-day window, inserted directly to simulate real history.
    old_entry = PauseHistoryEntry(
        tenant_id=TENANT, subscription_id=sub.id, member_profile_id=member_id,
        paused_at=datetime.now(timezone.utc) - timedelta(days=400), resumed_at=datetime.now(timezone.utc) - timedelta(days=395),
    )
    db_session.add(old_entry)
    await db_session.commit()

    count = await lifecycle_engine.count_pauses_in_window(db_session, TENANT, member_id, window_days=365)
    assert count == 0


async def test_pause_count_is_scoped_to_the_member_not_the_subscription(db_session):
    """Two different subscriptions for two different members must not
    bleed into each other's counts."""
    member_a = uuid.uuid4()
    member_b = uuid.uuid4()
    sub_a = await lifecycle_engine.create_subscription(db_session, TENANT, member_a, uuid.uuid4())
    await lifecycle_engine.create_subscription(db_session, TENANT, member_b, uuid.uuid4())
    resume_at = datetime.now(timezone.utc) + timedelta(days=30)

    await lifecycle_engine.pause_subscription(db_session, TENANT, sub_a.id, resume_at)

    assert await lifecycle_engine.count_pauses_in_window(db_session, TENANT, member_a) == 1
    assert await lifecycle_engine.count_pauses_in_window(db_session, TENANT, member_b) == 0
