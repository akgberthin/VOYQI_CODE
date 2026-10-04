import uuid

from src.models import KycStatus, MobilityClass, ServiceLevel
from src.services import member_engine, plan_engine

TENANT = "voyqi-holdings"


async def test_create_member_profile(db_session):
    profile = await member_engine.create_member_profile(db_session, tenant_id=TENANT, user_id="user-1", full_name="Priya S.", email="priya@example.com")
    assert profile.kyc_status == KycStatus.PENDING
    assert profile.gu_balance == 0


async def test_update_kyc_status(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-2", "Tom W.", "tom@example.com")
    updated = await member_engine.update_kyc_status(db_session, TENANT, profile.id, KycStatus.VERIFIED)
    assert updated.kyc_status == KycStatus.VERIFIED


async def test_get_member_by_user_id(db_session):
    await member_engine.create_member_profile(db_session, TENANT, "user-lookup-1", "Sign In Test", "signin@example.com")
    found = await member_engine.get_member_by_user_id(db_session, TENANT, "user-lookup-1")
    assert found is not None
    assert found.full_name == "Sign In Test"


async def test_get_member_by_user_id_returns_none_when_not_found(db_session):
    found = await member_engine.get_member_by_user_id(db_session, TENANT, "does-not-exist")
    assert found is None


async def test_update_contact_info_only_touches_supplied_fields(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-contact-1", "Original Name", "original@example.com")
    updated = await member_engine.update_contact_info(db_session, TENANT, profile.id, full_name="New Name")
    assert updated.full_name == "New Name"
    assert updated.email == "original@example.com"  # untouched


async def test_set_service_level(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-3", "Garth H.", "garth@example.com")
    updated = await member_engine.set_service_level(db_session, TENANT, profile.id, ServiceLevel.EXECUTIVE_PLUS)
    assert updated.service_level == ServiceLevel.EXECUTIVE_PLUS


async def test_search_members_by_class(db_session):
    p1 = await member_engine.create_member_profile(db_session, TENANT, "user-4", "A", "a@example.com")
    p1.mobility_class = MobilityClass.LUXURY
    await db_session.commit()
    await member_engine.create_member_profile(db_session, TENANT, "user-5", "B", "b@example.com")

    luxury_only = await member_engine.search_members(db_session, TENANT, mobility_class=MobilityClass.LUXURY)
    assert len(luxury_only) == 1
    assert luxury_only[0].full_name == "A"


async def test_update_unknown_member_returns_none(db_session):
    result = await member_engine.update_kyc_status(db_session, TENANT, uuid.uuid4(), KycStatus.VERIFIED)
    assert result is None


async def test_create_plan_marks_invitation_only(db_session):
    plan = await plan_engine.create_plan(db_session, TENANT, MobilityClass.LUXURY_PLUS, "miami", 2500.0)
    assert plan.is_invitation_only is True

    urban_plan = await plan_engine.create_plan(db_session, TENANT, MobilityClass.URBAN, "miami", 350.0)
    assert urban_plan.is_invitation_only is False


async def test_list_plans_filtered_by_market(db_session):
    await plan_engine.create_plan(db_session, TENANT, MobilityClass.URBAN, "miami", 350.0)
    await plan_engine.create_plan(db_session, TENANT, MobilityClass.URBAN, "london", 400.0)
    miami_plans = await plan_engine.list_plans(db_session, TENANT, market="miami")
    assert len(miami_plans) == 1


async def test_resolve_equivalent_class_with_rule(db_session):
    await plan_engine.create_class_equivalency(
        db_session, TENANT, MobilityClass.EXECUTIVE, "london", MobilityClass.LUXURY, notes="London executive maps to luxury fleet"
    )
    resolved = await plan_engine.resolve_equivalent_class(db_session, TENANT, MobilityClass.EXECUTIVE, "london")
    assert resolved == MobilityClass.LUXURY


async def test_resolve_equivalent_class_without_rule_falls_back(db_session):
    resolved = await plan_engine.resolve_equivalent_class(db_session, TENANT, MobilityClass.URBAN, "unconfigured_market")
    assert resolved == MobilityClass.URBAN
