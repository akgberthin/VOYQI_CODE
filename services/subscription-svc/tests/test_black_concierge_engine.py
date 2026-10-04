
from src.models import BlackInvitationStatus, ConciergeMode
from src.services import black_concierge_engine, member_engine

TENANT = "voyqi-holdings"


async def test_invite_and_accept_black(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-1", "Priya S.", "priya@example.com")
    invitation = await black_concierge_engine.invite_to_black(db_session, TENANT, profile.id, "black-ops-1")
    assert invitation.status == BlackInvitationStatus.INVITED

    accepted = await black_concierge_engine.respond_to_invitation(db_session, TENANT, invitation.id, accept=True)
    assert accepted.status == BlackInvitationStatus.ACCEPTED

    await db_session.refresh(profile)
    assert profile.is_voyqi_black is True


async def test_decline_black_invitation(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-2", "Tom W.", "tom@example.com")
    invitation = await black_concierge_engine.invite_to_black(db_session, TENANT, profile.id, "black-ops-1")
    declined = await black_concierge_engine.respond_to_invitation(db_session, TENANT, invitation.id, accept=False)
    assert declined.status == BlackInvitationStatus.DECLINED

    await db_session.refresh(profile)
    assert profile.is_voyqi_black is False


async def test_cannot_respond_twice(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-3", "A", "a@example.com")
    invitation = await black_concierge_engine.invite_to_black(db_session, TENANT, profile.id, "black-ops-1")
    await black_concierge_engine.respond_to_invitation(db_session, TENANT, invitation.id, accept=True)
    second = await black_concierge_engine.respond_to_invitation(db_session, TENANT, invitation.id, accept=False)
    assert second is None


async def test_list_black_members(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-4", "B", "b@example.com")
    invitation = await black_concierge_engine.invite_to_black(db_session, TENANT, profile.id, "black-ops-1")
    await black_concierge_engine.respond_to_invitation(db_session, TENANT, invitation.id, accept=True)
    black_members = await black_concierge_engine.list_black_members(db_session, TENANT)
    assert len(black_members) == 1


async def test_start_and_complete_journey(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-5", "C", "c@example.com")
    journey = await black_concierge_engine.start_journey(
        db_session, TENANT, profile.id, "airport_pickup", ConciergeMode.PROACTIVE, "concierge-1"
    )
    assert journey.status.value == "active"

    completed = await black_concierge_engine.complete_journey(db_session, TENANT, journey.id, satisfaction_score=5)
    assert completed.status.value == "completed"
    assert completed.satisfaction_score == 5


async def test_list_active_journeys(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-6", "D", "d@example.com")
    await black_concierge_engine.start_journey(db_session, TENANT, profile.id, "chauffeur", ConciergeMode.REACTIVE)
    active = await black_concierge_engine.list_active_journeys(db_session, TENANT)
    assert len(active) == 1


async def test_cannot_complete_journey_twice(db_session):
    profile = await member_engine.create_member_profile(db_session, TENANT, "user-7", "E", "e@example.com")
    journey = await black_concierge_engine.start_journey(db_session, TENANT, profile.id, "hotel_coordination", ConciergeMode.ASSISTED)
    await black_concierge_engine.complete_journey(db_session, TENANT, journey.id)
    second = await black_concierge_engine.complete_journey(db_session, TENANT, journey.id)
    assert second is None
