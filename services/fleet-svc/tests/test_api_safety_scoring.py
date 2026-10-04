import uuid

from tests.conftest import TENANT_HEADERS


async def test_driving_event_below_threshold_does_not_publish(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import safety_scoring
    monkeypatch.setattr(safety_scoring, "publish_event", spy)

    vehicle_id = str(uuid.uuid4())
    for _ in range(4):  # below the 5-event reassessment interval
        await client.post("/api/v1/fleet/driving-events", json={"vehicle_id": vehicle_id, "event_type": "harsh_braking"}, headers=TENANT_HEADERS)
    assert len(published) == 0


async def test_fifth_driving_event_publishes_safety_score_requested(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import safety_scoring
    monkeypatch.setattr(safety_scoring, "publish_event", spy)

    vehicle_id = str(uuid.uuid4())
    for _ in range(5):
        await client.post("/api/v1/fleet/driving-events", json={"vehicle_id": vehicle_id, "event_type": "speeding"}, headers=TENANT_HEADERS)
    assert len(published) == 1
    event_type, payload = published[0]
    assert event_type == "flt.safety_score_requested"
    assert payload["event_counts"]["speeding"] == 5


async def test_full_safety_score_flow_api(client):
    vehicle_id = str(uuid.uuid4())
    await client.post("/api/v1/fleet/driving-events", json={"vehicle_id": vehicle_id, "event_type": "phone_usage_detected"}, headers=TENANT_HEADERS)

    events_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/driving-events", headers=TENANT_HEADERS)
    assert len(events_resp.json()) == 1

    score_resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/safety-scores", params={"score": 78.0, "reasoning": "one phone-usage event in the window"}, headers=TENANT_HEADERS)
    assert score_resp.status_code == 201

    latest_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/safety-scores/latest", headers=TENANT_HEADERS)
    assert latest_resp.json()["score"] == 78.0


async def test_safety_watchlist_api(client):
    vehicle_id = str(uuid.uuid4())
    await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/safety-scores", params={"score": 45.0, "reasoning": "multiple harsh braking events"}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/fleet/safety-scores/watchlist", params={"threshold": 60.0}, headers=TENANT_HEADERS)
    assert len(resp.json()) >= 1
    assert all(s["score"] < 60.0 for s in resp.json())


async def test_assess_safety_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(
        ai_orchestration_tools, "request_safety_score",
        lambda tenant_id, vehicle_id, event_counts, window_days: {"score": 72.0, "reasoning": "x"},
    )
    vehicle_id = str(uuid.uuid4())
    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/assess-safety-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["score"] == 72.0
