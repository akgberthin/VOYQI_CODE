import uuid

import pytest

from src.models import MobilityClass, UpgradeCaseStatus
from src.services import (
    lifecycle_engine,
    member_engine,
    plan_engine,
    upgrade_intelligence_engine,
)
from src.services.upgrade_intelligence_engine import (
    NoPlanForClassError,
    UpgradeCaseNotOfferedError,
)

TENANT = "voyqi-holdings"


async def test_pattern_not_detected_below_threshold(db_session):
    member_id = uuid.uuid4()
    for _ in range(2):
        await upgrade_intelligence_engine.record_above_class_request(db_session, TENANT, member_id, uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY)

    top_class, count, _reasoning = await upgrade_intelligence_engine.detect_upgrade_pattern(db_session, TENANT, member_id)
    assert top_class is None
    assert count == 2


async def test_pattern_detected_at_threshold(db_session):
    member_id = uuid.uuid4()
    for _ in range(3):
        await upgrade_intelligence_engine.record_above_class_request(db_session, TENANT, member_id, uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY)

    top_class, count, _reasoning = await upgrade_intelligence_engine.detect_upgrade_pattern(db_session, TENANT, member_id)
    assert top_class == MobilityClass.LUXURY
    assert count == 3


async def test_pattern_picks_the_most_requested_class_when_mixed(db_session):
    member_id = uuid.uuid4()
    for _ in range(3):
        await upgrade_intelligence_engine.record_above_class_request(db_session, TENANT, member_id, uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY)
    for _ in range(1):
        await upgrade_intelligence_engine.record_above_class_request(db_session, TENANT, member_id, uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY_PLUS)

    top_class, count, _ = await upgrade_intelligence_engine.detect_upgrade_pattern(db_session, TENANT, member_id)
    assert top_class == MobilityClass.LUXURY
    assert count == 3


async def test_create_upgrade_case_is_offered_not_accepted(db_session):
    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "pattern met",
    )
    assert case.status == UpgradeCaseStatus.OFFERED


async def test_accept_upgrade_case_actually_changes_the_subscriptions_plan(db_session):
    member = await member_engine.create_member_profile(db_session, TENANT, "user-upgrade-1", "Upgrade Test", "upgrade@example.com")
    current_plan = await plan_engine.create_plan(db_session, tenant_id=TENANT, mobility_class=MobilityClass.EXECUTIVE, market="miami", monthly_price_usd=859.0, entitlements={})
    luxury_plan = await plan_engine.create_plan(db_session, tenant_id=TENANT, mobility_class=MobilityClass.LUXURY, market="miami", monthly_price_usd=1499.0, entitlements={})
    subscription = await lifecycle_engine.create_subscription(db_session, TENANT, member.id, current_plan.id)

    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, TENANT, member.id, subscription.id, MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "pattern met",
    )
    resolved_case, updated_subscription = await upgrade_intelligence_engine.accept_upgrade_case(db_session, TENANT, case.id)

    assert resolved_case.status == UpgradeCaseStatus.ACCEPTED
    assert updated_subscription.plan_id == luxury_plan.id


async def test_accept_upgrade_case_requires_a_plan_to_exist_for_the_recommended_class(db_session):
    member = await member_engine.create_member_profile(db_session, TENANT, "user-upgrade-2", "No Plan Test", "noplan@example.com")
    current_plan = await plan_engine.create_plan(db_session, tenant_id=TENANT, mobility_class=MobilityClass.EXECUTIVE, market="miami", monthly_price_usd=859.0, entitlements={})
    subscription = await lifecycle_engine.create_subscription(db_session, TENANT, member.id, current_plan.id)

    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, TENANT, member.id, subscription.id, MobilityClass.EXECUTIVE, MobilityClass.LUXURY_PLUS, 3, "pattern met — but no luxury_plus plan on file",
    )
    with pytest.raises(NoPlanForClassError):
        await upgrade_intelligence_engine.accept_upgrade_case(db_session, TENANT, case.id)


async def test_decline_upgrade_case_leaves_the_subscription_untouched(db_session):
    member = await member_engine.create_member_profile(db_session, TENANT, "user-upgrade-3", "Decline Test", "decline@example.com")
    current_plan = await plan_engine.create_plan(db_session, tenant_id=TENANT, mobility_class=MobilityClass.EXECUTIVE, market="miami", monthly_price_usd=859.0, entitlements={})
    subscription = await lifecycle_engine.create_subscription(db_session, TENANT, member.id, current_plan.id)

    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, TENANT, member.id, subscription.id, MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "pattern met",
    )
    declined = await upgrade_intelligence_engine.decline_upgrade_case(db_session, TENANT, case.id)

    assert declined.status == UpgradeCaseStatus.DECLINED
    assert subscription.plan_id == current_plan.id  # unchanged


async def test_cannot_accept_an_already_resolved_case(db_session):
    case = await upgrade_intelligence_engine.create_upgrade_case(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "pattern met",
    )
    await upgrade_intelligence_engine.decline_upgrade_case(db_session, TENANT, case.id)
    with pytest.raises(UpgradeCaseNotOfferedError):
        await upgrade_intelligence_engine.accept_upgrade_case(db_session, TENANT, case.id)


async def test_list_upgrade_cases_filters_by_member(db_session):
    member_a = uuid.uuid4()
    member_b = uuid.uuid4()
    await upgrade_intelligence_engine.create_upgrade_case(db_session, TENANT, member_a, uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY, 3, "x")
    await upgrade_intelligence_engine.create_upgrade_case(db_session, TENANT, member_b, uuid.uuid4(), MobilityClass.URBAN, MobilityClass.EXECUTIVE, 3, "x")

    cases = await upgrade_intelligence_engine.list_upgrade_cases(db_session, TENANT, member_profile_id=member_a)
    assert len(cases) == 1
    assert cases[0].member_profile_id == member_a


async def test_record_above_class_request_calls_agent_and_never_raises_on_failure(db_session):
    """ai-orchestration-svc isn't running in tests, so this exercises
    the real degrade-to-None path — the same safety property
    lifecycle_engine's winback call already relies on."""
    record, evaluation = await upgrade_intelligence_engine.record_above_class_request(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY
    )
    assert record.id is not None
    assert evaluation is None


async def test_record_above_class_request_surfaces_agent_result_when_available(db_session, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_upgrade_pattern_evaluation",
        lambda tenant_id, member_id, subscription_id, current_class: {"case_created": True, "reasoning": "pattern confirmed"},
    )
    _record, evaluation = await upgrade_intelligence_engine.record_above_class_request(
        db_session, TENANT, uuid.uuid4(), uuid.uuid4(), MobilityClass.EXECUTIVE, MobilityClass.LUXURY
    )
    assert evaluation == {"case_created": True, "reasoning": "pattern confirmed"}
