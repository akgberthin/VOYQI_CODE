import uuid

from src.services import fleet_tools
from tests.conftest import TENANT_HEADERS


async def test_swap_request_api_flow(client, monkeypatch):
    fake_vehicle_id = str(uuid.uuid4())
    fake_transfer_id = str(uuid.uuid4())
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda tenant_id, vehicle_class, hub_id=None: [{"id": fake_vehicle_id}])
    monkeypatch.setattr(fleet_tools, "propose_transfer", lambda tenant_id, vehicle_id, to_hub_id, reason: {"id": fake_transfer_id})

    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-1", "full_name": "Priya S.", "email": "priya@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    request_resp = await client.post(
        "/api/v1/subscription/swap-requests",
        json={"member_profile_id": member_id, "requested_class": "executive"},
        headers=TENANT_HEADERS,
    )
    assert request_resp.status_code == 201
    swap_id = request_resp.json()["id"]

    stage_resp = await client.post(
        f"/api/v1/subscription/swap-requests/{swap_id}/stage",
        json={"to_hub_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert stage_resp.json()["status"] == "staged"

    fulfill_resp = await client.post(f"/api/v1/subscription/swap-requests/{swap_id}/fulfill", headers=TENANT_HEADERS)
    assert fulfill_resp.json()["status"] == "fulfilled"


async def test_travel_declaration_api_flow(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "search_available_vehicles", lambda *a, **k: [])

    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-2", "full_name": "Tom W.", "email": "tom@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    declare_resp = await client.post(
        "/api/v1/subscription/travel-declarations",
        json={"member_profile_id": member_id, "source_class": "urban", "destination_market": "london", "destination_hub_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert declare_resp.status_code == 201
    declaration_id = declare_resp.json()["id"]

    complete_resp = await client.post(f"/api/v1/subscription/travel-declarations/{declaration_id}/complete", headers=TENANT_HEADERS)
    assert complete_resp.json()["is_active"] is False


async def test_black_invitation_and_journey_api_flow(client):
    member_resp = await client.post(
        "/api/v1/subscription/members",
        json={"user_id": "user-3", "full_name": "Garth H.", "email": "garth@example.com"},
        headers=TENANT_HEADERS,
    )
    member_id = member_resp.json()["id"]

    invite_resp = await client.post(
        "/api/v1/subscription/black-invitations",
        json={"member_profile_id": member_id, "invited_by": "black-ops-1"},
        headers=TENANT_HEADERS,
    )
    assert invite_resp.status_code == 201
    invitation_id = invite_resp.json()["id"]

    respond_resp = await client.post(
        f"/api/v1/subscription/black-invitations/{invitation_id}/respond", json={"accept": True}, headers=TENANT_HEADERS
    )
    assert respond_resp.json()["status"] == "accepted"

    black_members_resp = await client.get("/api/v1/subscription/black-members", headers=TENANT_HEADERS)
    assert len(black_members_resp.json()) == 1

    journey_resp = await client.post(
        "/api/v1/subscription/concierge-journeys",
        json={"member_profile_id": member_id, "journey_type": "airport_pickup", "mode": "proactive"},
        headers=TENANT_HEADERS,
    )
    assert journey_resp.status_code == 201
    journey_id = journey_resp.json()["id"]

    active_resp = await client.get("/api/v1/subscription/concierge-journeys/active", headers=TENANT_HEADERS)
    assert len(active_resp.json()) == 1

    complete_resp = await client.post(
        f"/api/v1/subscription/concierge-journeys/{journey_id}/complete", json={"satisfaction_score": 5}, headers=TENANT_HEADERS
    )
    assert complete_resp.json()["status"] == "completed"
