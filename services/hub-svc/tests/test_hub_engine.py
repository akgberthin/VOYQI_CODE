import uuid

from src.models import HubType
from src.services import hub_engine

TENANT = "voyqi-holdings"


async def test_create_hub(db_session):
    hub = await hub_engine.create_hub(
        db_session, TENANT, "Miami International Airport Hub", HubType.AIRPORT, 25.7959, -80.2870,
        address="4200 NW 21st St, Miami, FL", capacity=25, has_lounge=True, amenities=["valet", "lounge", "wifi"],
    )
    assert hub.hub_type == HubType.AIRPORT
    assert hub.has_lounge is True
    assert hub.is_active is True


async def test_list_hubs_filters_by_type(db_session):
    await hub_engine.create_hub(db_session, TENANT, "Airport Hub", HubType.AIRPORT, 25.79, -80.28)
    await hub_engine.create_hub(db_session, TENANT, "Downtown Hub", HubType.CITY_BUSINESS, 25.77, -80.19)
    airport_hubs = await hub_engine.list_hubs(db_session, TENANT, hub_type=HubType.AIRPORT)
    assert len(airport_hubs) == 1
    assert airport_hubs[0].hub_type == HubType.AIRPORT


async def test_update_capacity(db_session):
    hub = await hub_engine.create_hub(db_session, TENANT, "Hub", HubType.RESIDENTIAL, 25.7, -80.2, capacity=10)
    updated = await hub_engine.update_capacity(db_session, TENANT, hub.id, 20)
    assert updated.capacity == 20


async def test_update_capacity_unknown_hub_returns_none(db_session):
    result = await hub_engine.update_capacity(db_session, TENANT, uuid.uuid4(), 20)
    assert result is None


async def test_deactivate_hub(db_session):
    hub = await hub_engine.create_hub(db_session, TENANT, "Hub", HubType.PARTNER_WHITE_LABEL, 25.7, -80.2)
    deactivated = await hub_engine.deactivate_hub(db_session, TENANT, hub.id)
    assert deactivated.is_active is False

    active_only = await hub_engine.list_hubs(db_session, TENANT, is_active=True)
    assert hub.id not in [h.id for h in active_only]
