from tests.conftest import TENANT_HEADERS


async def _certified_vehicle_via_api(client, vin="1HGCM82633A004352"):
    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders",
        json={"source": "dealer", "vehicle_description": "test", "cost_usd": 50000.0, "created_by": "fleet-ops-1"},
        headers=TENANT_HEADERS,
    )
    po_id = po_resp.json()["id"]
    receive_resp = await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/receive",
        json={"vin": vin, "make": "Test", "model": "Car", "year": 2024, "vehicle_class": "executive"},
        headers=TENANT_HEADERS,
    )
    vehicle_id = receive_resp.json()["id"]
    await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/provision",
        json={"provisioned_by": "fleet-ops-1", "card_id": "VQ-1"},
        headers=TENANT_HEADERS,
    )
    await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/certify-class", json={"certified_by": "fleet-ops-1"}, headers=TENANT_HEADERS
    )
    return vehicle_id


async def test_staging_api_flow(client):
    vehicle_id = await _certified_vehicle_via_api(client)
    await client.post("/api/v1/fleet/hubs", json={"name": "Brickell"}, headers=TENANT_HEADERS)
    hub_b = await client.post("/api/v1/fleet/hubs", json={"name": "Wynwood"}, headers=TENANT_HEADERS)

    transfer_resp = await client.post(
        "/api/v1/fleet/transfer-orders",
        json={"vehicle_id": vehicle_id, "to_hub_id": hub_b.json()["id"], "reason": "demand"},
        headers=TENANT_HEADERS,
    )
    assert transfer_resp.status_code == 201
    transfer_id = transfer_resp.json()["id"]

    dispatch_resp = await client.post(
        f"/api/v1/fleet/transfer-orders/{transfer_id}/dispatch",
        json={"dispatched_by": "fleet-ops-1", "driver_id": "driver-1"},
        headers=TENANT_HEADERS,
    )
    assert dispatch_resp.json()["status"] == "dispatched"

    complete_resp = await client.post(f"/api/v1/fleet/transfer-orders/{transfer_id}/complete", headers=TENANT_HEADERS)
    assert complete_resp.json()["status"] == "completed"


async def test_maintenance_api_flow(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004353")
    alert_resp = await client.post(
        "/api/v1/fleet/maintenance-alerts/evaluate",
        json={"vehicle_id": vehicle_id, "miles_since_service": 100, "dtc_codes": ["P0301"]},
        headers=TENANT_HEADERS,
    )
    assert alert_resp.json()["alert_created"] is True
    alert_id = alert_resp.json()["alert"]["id"]

    wo_resp = await client.post(
        "/api/v1/fleet/work-orders",
        json={"vehicle_id": vehicle_id, "vendor": "Service Center", "created_by": "fleet-ops-1", "alert_id": alert_id},
        headers=TENANT_HEADERS,
    )
    assert wo_resp.status_code == 201
    wo_id = wo_resp.json()["id"]

    complete_resp = await client.post(f"/api/v1/fleet/work-orders/{wo_id}/complete", headers=TENANT_HEADERS)
    assert complete_resp.json()["status"] == "completed"


async def test_incidents_api_flow(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004354")
    incident_resp = await client.post(
        "/api/v1/fleet/incidents",
        json={"vehicle_id": vehicle_id, "incident_type": "accident", "description": "collision", "reported_by": "member-1"},
        headers=TENANT_HEADERS,
    )
    assert incident_resp.status_code == 201
    incident_id = incident_resp.json()["id"]

    assess_resp = await client.post(
        f"/api/v1/fleet/incidents/{incident_id}/assess-liability",
        json={"liability": "member", "assessed_by": "risk-team-1"},
        headers=TENANT_HEADERS,
    )
    assert assess_resp.json()["liability_assessment"] == "member"

    total_loss_resp = await client.post(
        f"/api/v1/fleet/incidents/{incident_id}/total-loss",
        json={"declared_by": "risk-team-1", "reason": "repair exceeds value"},
        headers=TENANT_HEADERS,
    )
    assert total_loss_resp.json()["status"] == "total_loss"


async def test_telematics_api_flow(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004355")
    ingest_resp = await client.post(
        "/api/v1/fleet/telemetry",
        json={"vehicle_id": vehicle_id, "latitude": 25.76, "longitude": -80.19, "odometer_miles": 4000},
        headers=TENANT_HEADERS,
    )
    assert ingest_resp.status_code == 201

    tamper_resp = await client.post(
        "/api/v1/fleet/tamper-events",
        json={"vehicle_id": vehicle_id, "detected_signal": "cert mismatch"},
        headers=TENANT_HEADERS,
    )
    assert tamper_resp.status_code == 201


async def test_depreciation_remarketing_floorplan_api_flow(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004356")

    schedule_resp = await client.post(
        "/api/v1/fleet/depreciation-schedules",
        json={"vehicle_id": vehicle_id, "acquisition_cost_usd": 60000.0, "salvage_value_usd": 24000.0, "useful_life_months": 36},
        headers=TENANT_HEADERS,
    )
    assert schedule_resp.status_code == 201

    book_value_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/book-value", headers=TENANT_HEADERS)
    assert book_value_resp.status_code == 200

    eligibility_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}/disposition-eligibility", headers=TENANT_HEADERS)
    assert eligibility_resp.json()["eligible"] is False

    facility_resp = await client.post(
        "/api/v1/fleet/floorplan-facilities",
        json={"lender": "Ally Financial", "credit_limit_usd": 5000000.0},
        headers=TENANT_HEADERS,
    )
    facility_id = facility_resp.json()["id"]

    pledge_resp = await client.post(
        "/api/v1/fleet/vin-collateral/pledge",
        json={"vehicle_id": vehicle_id, "facility_id": facility_id, "pledged_amount_usd": 60000.0},
        headers=TENANT_HEADERS,
    )
    assert pledge_resp.status_code == 201


async def test_geofence_alert_publishes_anomaly_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import telematics
    monkeypatch.setattr(telematics, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004370")
    hub_resp = await client.post("/api/v1/fleet/hubs", json={"name": "Test Hub"}, headers=TENANT_HEADERS)

    resp = await client.post(
        "/api/v1/fleet/geofence-check",
        json={"vehicle_id": vehicle_id, "hub_id": hub_resp.json()["id"], "vehicle_lat": 40.0, "vehicle_lon": -74.0, "hub_lat": 25.0, "hub_lon": -80.0},
        headers=TENANT_HEADERS,
    )
    assert resp.json()["alert_created"] is True
    event_type, payload = published[0]
    assert event_type == "flt.telemetry_anomaly_requested"
    assert payload["distance_miles"] > 50.0


async def test_geofence_within_radius_publishes_no_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import telematics
    monkeypatch.setattr(telematics, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004371")
    hub_resp = await client.post("/api/v1/fleet/hubs", json={"name": "Test Hub"}, headers=TENANT_HEADERS)

    resp = await client.post(
        "/api/v1/fleet/geofence-check",
        json={"vehicle_id": vehicle_id, "hub_id": hub_resp.json()["id"], "vehicle_lat": 25.76, "vehicle_lon": -80.19, "hub_lat": 25.77, "hub_lon": -80.20},
        headers=TENANT_HEADERS,
    )
    assert resp.json()["alert_created"] is False
    assert len(published) == 0


async def test_assess_anomaly_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_telemetry_anomaly", lambda tenant_id, alert_id, distance: {"assessment_created": True, "reasoning": "x"})

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004372")
    hub_resp = await client.post("/api/v1/fleet/hubs", json={"name": "Test Hub"}, headers=TENANT_HEADERS)
    geo_resp = await client.post(
        "/api/v1/fleet/geofence-check",
        json={"vehicle_id": vehicle_id, "hub_id": hub_resp.json()["id"], "vehicle_lat": 40.0, "vehicle_lon": -74.0, "hub_lat": 25.0, "hub_lon": -80.0},
        headers=TENANT_HEADERS,
    )
    alert_id = geo_resp.json()["alert_id"]

    resp = await client.post(f"/api/v1/fleet/geofence-alerts/{alert_id}/assess-anomaly-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["assessment_created"] is True


async def test_assess_anomaly_now_unknown_alert_404(client):
    import uuid
    resp = await client.post(f"/api/v1/fleet/geofence-alerts/{uuid.uuid4()}/assess-anomaly-now", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_create_and_get_anomaly_assessment_api(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004373")
    hub_resp = await client.post("/api/v1/fleet/hubs", json={"name": "Test Hub"}, headers=TENANT_HEADERS)
    geo_resp = await client.post(
        "/api/v1/fleet/geofence-check",
        json={"vehicle_id": vehicle_id, "hub_id": hub_resp.json()["id"], "vehicle_lat": 40.0, "vehicle_lon": -74.0, "hub_lat": 25.0, "hub_lon": -80.0},
        headers=TENANT_HEADERS,
    )
    alert_id = geo_resp.json()["alert_id"]

    create_resp = await client.post(f"/api/v1/fleet/geofence-alerts/{alert_id}/anomaly-assessments", json={"assessment": "needs_investigation", "reasoning": "large excursion"}, headers=TENANT_HEADERS)
    assert create_resp.status_code == 200
    assert create_resp.json()["assessment"] == "needs_investigation"

    get_resp = await client.get(f"/api/v1/fleet/geofence-alerts/{alert_id}/anomaly-assessment", headers=TENANT_HEADERS)
    assert get_resp.json()["assessment"] == "needs_investigation"


async def test_get_anomaly_assessment_unknown_alert_404(client):
    import uuid
    resp = await client.get(f"/api/v1/fleet/geofence-alerts/{uuid.uuid4()}/anomaly-assessment", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_maintenance_triage_triggers_after_threshold_alerts(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import maintenance
    monkeypatch.setattr(maintenance, "publish_event", spy)

    for i in range(3):
        vehicle_id = await _certified_vehicle_via_api(client, vin=f"1HGCM82633A00439{i}")
        await client.post("/api/v1/fleet/maintenance-alerts/evaluate", json={"vehicle_id": vehicle_id, "miles_since_service": 6000, "dtc_codes": ["P0300"]}, headers=TENANT_HEADERS)

    triage_events = [p for e, p in published if e == "flt.maintenance_triage_requested"]
    assert len(triage_events) == 1
    assert len(triage_events[0]["alerts"]) == 3


async def test_maintenance_triage_does_not_trigger_below_threshold(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import maintenance
    monkeypatch.setattr(maintenance, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004395")
    await client.post("/api/v1/fleet/maintenance-alerts/evaluate", json={"vehicle_id": vehicle_id, "miles_since_service": 6000, "dtc_codes": ["P0300"]}, headers=TENANT_HEADERS)

    assert len(published) == 0


async def test_triage_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_maintenance_triage", lambda tenant_id, alerts: {"triage_created": True, "priority_order": []})

    resp = await client.post("/api/v1/fleet/maintenance-alerts/triage-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["triage_created"] is True


async def test_create_and_get_triage_assessment_api(client):
    resp = await client.post(
        "/api/v1/fleet/maintenance-triage-assessments", json={"alert_ids": ["a1", "a2"], "priority_order": ["veh-1", "veh-2"], "reasoning": "urgent DTC first"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200

    get_resp = await client.get("/api/v1/fleet/maintenance-triage-assessments/latest", headers=TENANT_HEADERS)
    assert get_resp.json()["priority_order"] == ["veh-1", "veh-2"]


async def test_incident_report_publishes_severity_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import incidents
    monkeypatch.setattr(incidents, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004396")
    resp = await client.post(
        "/api/v1/fleet/incidents", json={"vehicle_id": vehicle_id, "incident_type": "accident", "description": "airbag deployed, possible injury", "reported_by": "member-1"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 201
    event_type, payload = published[0]
    assert event_type == "flt.incident_severity_requested"
    assert "airbag" in payload["description"]


async def test_assess_severity_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_incident_severity", lambda tenant_id, incident_id, incident_type, description: {"severity": "severe", "reasoning": "x"})

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004397")
    incident_resp = await client.post(
        "/api/v1/fleet/incidents", json={"vehicle_id": vehicle_id, "incident_type": "accident", "description": "collision", "reported_by": "member-1"}, headers=TENANT_HEADERS
    )

    resp = await client.post(f"/api/v1/fleet/incidents/{incident_resp.json()['id']}/assess-severity-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["severity"] == "severe"


async def test_create_and_get_severity_assessment_api(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004398")
    incident_resp = await client.post(
        "/api/v1/fleet/incidents", json={"vehicle_id": vehicle_id, "incident_type": "damage", "description": "small scratch", "reported_by": "member-1"}, headers=TENANT_HEADERS
    )
    incident_id = incident_resp.json()["id"]

    create_resp = await client.post(f"/api/v1/fleet/incidents/{incident_id}/severity-assessments", json={"severity": "minor", "summary": "cosmetic only", "reasoning": "no severe keywords"}, headers=TENANT_HEADERS)
    assert create_resp.status_code == 200
    assert create_resp.json()["severity"] == "minor"

    get_resp = await client.get(f"/api/v1/fleet/incidents/{incident_id}/severity", headers=TENANT_HEADERS)
    assert get_resp.json()["severity"] == "minor"


async def test_get_severity_unknown_incident_404(client):
    import uuid
    resp = await client.get(f"/api/v1/fleet/incidents/{uuid.uuid4()}/severity", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_assess_severity_now_unknown_incident_404(client):
    import uuid
    resp = await client.post(f"/api/v1/fleet/incidents/{uuid.uuid4()}/assess-severity-now", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_asset_valuation_publishes_anomaly_event_when_schedule_exists(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import depreciation
    monkeypatch.setattr(depreciation, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004420")
    await client.post(
        "/api/v1/fleet/depreciation-schedules", json={"vehicle_id": vehicle_id, "acquisition_cost_usd": 60000.0, "salvage_value_usd": 24000.0, "useful_life_months": 36}, headers=TENANT_HEADERS
    )

    resp = await client.post("/api/v1/fleet/asset-valuations", json={"vehicle_id": vehicle_id, "market_value_estimate_usd": 30000.0, "valued_by": "appraiser-1"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    event_type, payload = published[0]
    assert event_type == "flt.valuation_anomaly_requested"
    assert payload["market_value_usd"] == 30000.0


async def test_asset_valuation_no_schedule_publishes_no_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import depreciation
    monkeypatch.setattr(depreciation, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004421")
    resp = await client.post("/api/v1/fleet/asset-valuations", json={"vehicle_id": vehicle_id, "market_value_estimate_usd": 30000.0, "valued_by": "appraiser-1"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert len(published) == 0


async def test_assess_valuation_anomaly_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_valuation_anomaly", lambda tenant_id, vehicle_id, valuation_id, book, market: {"assessment": "flagged", "reasoning": "x"})

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004422")
    await client.post(
        "/api/v1/fleet/depreciation-schedules", json={"vehicle_id": vehicle_id, "acquisition_cost_usd": 60000.0, "salvage_value_usd": 24000.0, "useful_life_months": 36}, headers=TENANT_HEADERS
    )
    val_resp = await client.post("/api/v1/fleet/asset-valuations", json={"vehicle_id": vehicle_id, "market_value_estimate_usd": 30000.0, "valued_by": "appraiser-1"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/asset-valuations/{val_resp.json()['id']}/assess-anomaly-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["assessment"] == "flagged"


async def test_assess_valuation_anomaly_now_no_schedule_409(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004423")
    val_resp = await client.post("/api/v1/fleet/asset-valuations", json={"vehicle_id": vehicle_id, "market_value_estimate_usd": 30000.0, "valued_by": "appraiser-1"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/asset-valuations/{val_resp.json()['id']}/assess-anomaly-now", headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_create_and_get_valuation_anomaly_api(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004424")
    val_resp = await client.post("/api/v1/fleet/asset-valuations", json={"vehicle_id": vehicle_id, "market_value_estimate_usd": 30000.0, "valued_by": "appraiser-1"}, headers=TENANT_HEADERS)

    create_resp = await client.post(
        f"/api/v1/fleet/asset-valuations/{val_resp.json()['id']}/anomaly-assessments", json={"vehicle_id": vehicle_id, "deviation_pct": -0.35, "assessment": "flagged", "reasoning": "well below book"}, headers=TENANT_HEADERS
    )
    assert create_resp.status_code == 200

    get_resp = await client.get(f"/api/v1/fleet/asset-valuations/{val_resp.json()['id']}/anomaly-assessment", headers=TENANT_HEADERS)
    assert get_resp.json()["assessment"] == "flagged"


async def test_get_valuation_anomaly_unknown_valuation_404(client):
    import uuid
    resp = await client.get(f"/api/v1/fleet/asset-valuations/{uuid.uuid4()}/anomaly-assessment", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def _disposition_eligible_vehicle_via_api(client, vin):
    vehicle_id = await _certified_vehicle_via_api(client, vin=vin)
    await client.post("/api/v1/fleet/telemetry", json={"vehicle_id": vehicle_id, "latitude": 25.7, "longitude": -80.2, "odometer_miles": 65000}, headers=TENANT_HEADERS)
    return vehicle_id


async def test_request_disposition_recommendation_publishes_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import remarketing
    monkeypatch.setattr(remarketing, "publish_event", spy)

    vehicle_id = await _disposition_eligible_vehicle_via_api(client, "1HGCM82633A004440")
    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/request-disposition-recommendation", headers=TENANT_HEADERS)
    assert resp.status_code == 202
    event_type, payload = published[0]
    assert event_type == "flt.disposition_channel_requested"
    assert payload["odometer_miles"] == 65000


async def test_request_disposition_recommendation_ineligible_vehicle_409(client):
    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004441")  # low mileage, not eligible
    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/request-disposition-recommendation", headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_recommend_disposition_channel_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_disposition_channel", lambda tenant_id, vehicle_id, odometer, vclass: {"recommended_channel": "rideshare", "reasoning": "x"})

    vehicle_id = await _disposition_eligible_vehicle_via_api(client, "1HGCM82633A004442")
    resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/recommend-disposition-channel-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["recommended_channel"] == "rideshare"


async def test_disposition_recommendation_creates_real_order_autonomously(client):
    """FLT-007 is now fully autonomous: the agent's tool call creates
    a real DispositionOrder in the same request — no PENDING state,
    no separate accept step."""
    vehicle_id = await _disposition_eligible_vehicle_via_api(client, "1HGCM82633A004443")

    create_resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/disposition-recommendations", json={"channel": "rideshare", "reasoning": "still viable"}, headers=TENANT_HEADERS)
    assert create_resp.status_code == 200
    body = create_resp.json()
    assert body["status"] == "accepted"
    assert body["channel"] == "rideshare"
    assert body["order_id"]

    # Nothing should be left pending — the order already exists.
    pending_resp = await client.get("/api/v1/fleet/disposition-recommendations/pending", headers=TENANT_HEADERS)
    assert all(r["id"] != body["recommendation_id"] for r in pending_resp.json())

    # The vehicle should have moved to REMARKETING immediately.
    vehicle_resp = await client.get(f"/api/v1/fleet/vehicles/{vehicle_id}", headers=TENANT_HEADERS)
    assert vehicle_resp.json()["status"] == "remarketing"


async def test_disposition_recommendation_autonomous_channel_choice_honored(client):
    vehicle_id = await _disposition_eligible_vehicle_via_api(client, "1HGCM82633A004444")
    create_resp = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/disposition-recommendations", json={"channel": "parts", "reasoning": "high mileage"}, headers=TENANT_HEADERS)
    assert create_resp.json()["channel"] == "parts"


async def test_disposition_recommendation_creates_distinct_orders_each_call(client):
    """Unlike the old PENDING/accept flow, there's no double-accept
    state to guard against — each autonomous call is its own
    completed action. Two separate requests create two separate
    orders, which is the expected behavior for a fully autonomous
    system (idempotency, if desired, belongs at the caller/event layer,
    not as an artificial approval gate)."""
    vehicle_id = await _disposition_eligible_vehicle_via_api(client, "1HGCM82633A004446")
    first = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/disposition-recommendations", json={"channel": "rideshare", "reasoning": "x"}, headers=TENANT_HEADERS)
    second = await client.post(f"/api/v1/fleet/vehicles/{vehicle_id}/disposition-recommendations", json={"channel": "rideshare", "reasoning": "x"}, headers=TENANT_HEADERS)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["order_id"] != second.json()["order_id"]


async def test_paused_agent_advisory_fallback_still_available(client, db_session):
    """The PENDING/accept/reject path in remarketing_engine.py remains
    available for the one deliberate fallback case: an agent that's
    been PAUSED still needs a way to leave an advisory note without
    creating real orders. This exercises that fallback path directly
    at the engine level, bypassing the (now-autonomous) API endpoint,
    which has no route for the PENDING-only path by design — a paused
    agent's fallback is an internal safety net, not a normal
    operator-facing flow."""
    import uuid as uuid_module

    from src.models import DispositionChannel
    from src.services import remarketing_engine

    vehicle_id_str = await _disposition_eligible_vehicle_via_api(client, "1HGCM82633A004445")
    rec = await remarketing_engine.create_disposition_channel_recommendation(
        db_session, TENANT_HEADERS["X-Tenant-Id"], uuid_module.UUID(vehicle_id_str), DispositionChannel.EXPORT, "paused-agent fallback note",
    )
    assert rec.status.value == "pending"


async def test_pledge_publishes_covenant_risk_event_on_every_pledge(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import floorplan
    monkeypatch.setattr(floorplan, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004450")
    facility_resp = await client.post("/api/v1/fleet/floorplan-facilities", json={"lender": "Ally Financial", "credit_limit_usd": 100000.0, "max_utilization_pct": 90.0}, headers=TENANT_HEADERS)

    await client.post(
        "/api/v1/fleet/vin-collateral/pledge", json={"vehicle_id": vehicle_id, "facility_id": facility_resp.json()["id"], "pledged_amount_usd": 30000.0}, headers=TENANT_HEADERS
    )
    risk_events = [p for e, p in published if e == "flt.covenant_risk_requested"]
    assert len(risk_events) == 1
    assert risk_events[0]["utilization_pct"] == 30.0
    # a low-utilization pledge should NOT also trigger the breach event
    breach_events = [p for e, p in published if e == "floorplan.covenant_breached"]
    assert len(breach_events) == 0


async def test_pledge_over_covenant_publishes_both_events(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import floorplan
    monkeypatch.setattr(floorplan, "publish_event", spy)

    vehicle_id = await _certified_vehicle_via_api(client, vin="1HGCM82633A004451")
    facility_resp = await client.post("/api/v1/fleet/floorplan-facilities", json={"lender": "Ally Financial", "credit_limit_usd": 100000.0, "max_utilization_pct": 50.0}, headers=TENANT_HEADERS)

    await client.post(
        "/api/v1/fleet/vin-collateral/pledge", json={"vehicle_id": vehicle_id, "facility_id": facility_resp.json()["id"], "pledged_amount_usd": 60000.0}, headers=TENANT_HEADERS
    )
    event_types = [e for e, p in published]
    assert "floorplan.covenant_breached" in event_types
    assert "flt.covenant_risk_requested" in event_types


async def test_assess_covenant_risk_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_covenant_risk", lambda tenant_id, facility_id, util, max_util: {"risk_level": "approaching", "reasoning": "x"})

    facility_resp = await client.post("/api/v1/fleet/floorplan-facilities", json={"lender": "Ally Financial", "credit_limit_usd": 100000.0}, headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/fleet/floorplan-facilities/{facility_resp.json()['id']}/assess-covenant-risk-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["risk_level"] == "approaching"


async def test_assess_covenant_risk_now_unknown_facility_404(client):
    import uuid
    resp = await client.post(f"/api/v1/fleet/floorplan-facilities/{uuid.uuid4()}/assess-covenant-risk-now", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_create_and_get_covenant_risk_api(client):
    facility_resp = await client.post("/api/v1/fleet/floorplan-facilities", json={"lender": "Ally Financial", "credit_limit_usd": 100000.0}, headers=TENANT_HEADERS)
    facility_id = facility_resp.json()["id"]

    create_resp = await client.post(f"/api/v1/fleet/floorplan-facilities/{facility_id}/covenant-risk-assessments", json={"risk_level": "safe", "reasoning": "well within limit"}, headers=TENANT_HEADERS)
    assert create_resp.status_code == 200

    get_resp = await client.get(f"/api/v1/fleet/floorplan-facilities/{facility_id}/covenant-risk", headers=TENANT_HEADERS)
    assert get_resp.json()["risk_level"] == "safe"


async def test_get_covenant_risk_unknown_facility_404(client):
    import uuid
    resp = await client.get(f"/api/v1/fleet/floorplan-facilities/{uuid.uuid4()}/covenant-risk", headers=TENANT_HEADERS)
    assert resp.status_code == 404
