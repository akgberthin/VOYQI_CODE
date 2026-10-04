import uuid

from tests.conftest import TENANT_HEADERS


async def test_full_hub_directory_flow_api(client):
    resp = await client.post(
        "/api/v1/hub/hubs",
        json={"name": "Miami Airport Hub", "hub_type": "airport", "latitude": 25.7959, "longitude": -80.2870, "capacity": 25, "has_lounge": True, "amenities": ["valet", "lounge"]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    hub_id = resp.json()["id"]

    get_resp = await client.get(f"/api/v1/hub/hubs/{hub_id}", headers=TENANT_HEADERS)
    assert get_resp.json()["hub_type"] == "airport"

    cap_resp = await client.patch(f"/api/v1/hub/hubs/{hub_id}/capacity", json={"capacity": 30}, headers=TENANT_HEADERS)
    assert cap_resp.json()["capacity"] == 30

    list_resp = await client.get("/api/v1/hub/hubs", params={"hub_type": "airport"}, headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_get_unknown_hub_404(client):
    resp = await client.get(f"/api/v1/hub/hubs/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_full_touchpoint_and_checklist_flow_api(client):
    hub_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "Downtown Hub", "hub_type": "city_business", "latitude": 25.77, "longitude": -80.19},
        headers=TENANT_HEADERS,
    )
    hub_id = hub_resp.json()["id"]

    touchpoint_resp = await client.post(
        "/api/v1/hub/touchpoints",
        json={"hub_id": hub_id, "event_type": "pickup", "source": "subscription", "member_or_renter_ref": "member-1"},
        headers=TENANT_HEADERS,
    )
    assert touchpoint_resp.status_code == 201
    event_id = touchpoint_resp.json()["id"]

    checklist_resp = await client.post(
        "/api/v1/hub/prep-checklists",
        json={"hub_id": hub_id, "vehicle_id": str(uuid.uuid4()), "item_labels": ["Wash", "Fuel check"], "touchpoint_event_id": event_id},
        headers=TENANT_HEADERS,
    )
    checklist_id = checklist_resp.json()["id"]

    blocked = await client.post(f"/api/v1/hub/prep-checklists/{checklist_id}/mark-ready", headers=TENANT_HEADERS)
    assert blocked.status_code == 409

    items_resp = await client.get(f"/api/v1/hub/prep-checklists/{checklist_id}/items", headers=TENANT_HEADERS)
    for item in items_resp.json():
        check_resp = await client.post(f"/api/v1/hub/prep-checklist-items/{item['id']}/check", headers=TENANT_HEADERS)
        assert check_resp.json()["is_completed"] is True

    ready_resp = await client.post(f"/api/v1/hub/prep-checklists/{checklist_id}/mark-ready", headers=TENANT_HEADERS)
    assert ready_resp.status_code == 200
    assert ready_resp.json()["status"] == "completed"

    complete_resp = await client.post(f"/api/v1/hub/touchpoints/{event_id}/complete", headers=TENANT_HEADERS)
    assert complete_resp.json()["status"] == "completed"


async def test_exception_flag_api(client):
    hub_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "Hub", "hub_type": "residential", "latitude": 25.7, "longitude": -80.2}, headers=TENANT_HEADERS
    )
    touchpoint_resp = await client.post(
        "/api/v1/hub/touchpoints",
        json={"hub_id": hub_resp.json()["id"], "event_type": "return", "source": "rental", "member_or_renter_ref": "renter-1"},
        headers=TENANT_HEADERS,
    )
    resp = await client.post(
        f"/api/v1/hub/touchpoints/{touchpoint_resp.json()['id']}/flag-exception", json={"reason": "vehicle damage found"}, headers=TENANT_HEADERS
    )
    assert resp.json()["status"] == "exception"
    assert resp.json()["exception_reason"] == "vehicle damage found"


async def test_card_geographic_anomaly_publishes_event_api(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import ops_board
    monkeypatch.setattr(ops_board, "publish_event", spy)

    miami_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "Miami Hub", "hub_type": "city_business", "latitude": 25.7959, "longitude": -80.2870}, headers=TENANT_HEADERS
    )
    la_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "LA Hub", "hub_type": "city_business", "latitude": 34.0522, "longitude": -118.2437}, headers=TENANT_HEADERS
    )
    card_resp = await client.post(
        "/api/v1/hub/cards", json={"member_profile_id": "member-1", "card_number": "VQ-9001-UC-STD-2026"}, headers=TENANT_HEADERS
    )
    card_id = card_resp.json()["id"]

    first_tap = await client.post("/api/v1/hub/card-taps", json={"hub_id": miami_resp.json()["id"], "card_id": card_id}, headers=TENANT_HEADERS)
    assert first_tap.json()["anomaly_flagged"] is False

    second_tap = await client.post("/api/v1/hub/card-taps", json={"hub_id": la_resp.json()["id"], "card_id": card_id}, headers=TENANT_HEADERS)
    assert second_tap.json()["anomaly_flagged"] is True
    assert len(published) == 1
    event_type, payload = published[0]
    assert event_type == "card.geographic_anomaly_detected"
    assert payload["card_id"] == card_id

    reactivate_resp = await client.post(f"/api/v1/hub/cards/{card_id}/reactivate", headers=TENANT_HEADERS)
    assert reactivate_resp.json()["status"] == "active"


async def test_analytics_endpoints_api(client):
    hub_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "Hub", "hub_type": "airport", "latitude": 25.7, "longitude": -80.2, "capacity": 5}, headers=TENANT_HEADERS
    )
    hub_id = hub_resp.json()["id"]
    await client.post(
        "/api/v1/hub/touchpoints", json={"hub_id": hub_id, "event_type": "pickup", "source": "subscription", "member_or_renter_ref": "m1"}, headers=TENANT_HEADERS
    )

    throughput = await client.get(f"/api/v1/hub/hubs/{hub_id}/throughput", headers=TENANT_HEADERS)
    assert throughput.status_code == 200

    dwell = await client.get(f"/api/v1/hub/hubs/{hub_id}/dwell-time", headers=TENANT_HEADERS)
    assert dwell.json()["sample_size"] == 0

    utilization = await client.get(f"/api/v1/hub/hubs/{hub_id}/utilization", headers=TENANT_HEADERS)
    assert utilization.json()["vehicles_scheduled"] == 1
    assert utilization.json()["utilization_pct"] == 20.0

    snapshot = await client.get("/api/v1/hub/network-snapshot", headers=TENANT_HEADERS)
    assert len(snapshot.json()) == 1


async def test_exception_flag_publishes_event_api(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import ops_board
    monkeypatch.setattr(ops_board, "publish_event", spy)

    hub_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "UT Austin Hub", "hub_type": "university_campus", "latitude": 30.28, "longitude": -97.73}, headers=TENANT_HEADERS
    )
    touchpoint_resp = await client.post(
        "/api/v1/hub/touchpoints",
        json={"hub_id": hub_resp.json()["id"], "event_type": "pickup", "source": "subscription", "member_or_renter_ref": "member-1088"},
        headers=TENANT_HEADERS,
    )
    resp = await client.post(
        f"/api/v1/hub/touchpoints/{touchpoint_resp.json()['id']}/flag-exception", json={"reason": "vehicle not yet staged"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert len(published) == 1
    event_type, payload = published[0]
    assert event_type == "hub.touchpoint_exception_flagged"
    assert payload["hub_id"] == hub_resp.json()["id"]
    assert payload["reason"] == "vehicle not yet staged"


async def test_network_snapshot_includes_geo_api(client):
    await client.post(
        "/api/v1/hub/hubs", json={"name": "Hub", "hub_type": "airport", "latitude": 25.79, "longitude": -80.28, "capacity": 10}, headers=TENANT_HEADERS
    )
    snapshot = await client.get("/api/v1/hub/network-snapshot", headers=TENANT_HEADERS)
    assert snapshot.json()[0]["latitude"] == 25.79
    assert snapshot.json()[0]["longitude"] == -80.28


async def test_tap_history_endpoint(client):
    hub_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "Miami Hub", "hub_type": "city_business", "latitude": 25.79, "longitude": -80.28}, headers=TENANT_HEADERS
    )
    card_resp = await client.post(
        "/api/v1/hub/cards", json={"member_profile_id": "member-1", "card_number": "VQ-2001-UC-STD-2026"}, headers=TENANT_HEADERS
    )
    await client.post("/api/v1/hub/card-taps", json={"hub_id": hub_resp.json()["id"], "card_id": card_resp.json()["id"]}, headers=TENANT_HEADERS)

    history_resp = await client.get(f"/api/v1/hub/cards/{card_resp.json()['id']}/tap-history", headers=TENANT_HEADERS)
    assert len(history_resp.json()) == 1
    assert history_resp.json()[0]["hub_name"] == "Miami Hub"


async def test_rebalancing_recommendation_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_hub_rebalancing",
        lambda tenant_id, source_hub_id, reason: {"candidate_hub_id": "hub-2", "reasoning": "Coral Gables has headroom and is nearby"},
    )
    hub_resp = await client.post(
        "/api/v1/hub/hubs", json={"name": "Downtown Miami", "hub_type": "city_business", "latitude": 25.77, "longitude": -80.19}, headers=TENANT_HEADERS
    )
    resp = await client.post(
        f"/api/v1/hub/hubs/{hub_resp.json()['id']}/rebalancing-recommendation", json={"reason": "testing"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_hub_id"] == "hub-2"
