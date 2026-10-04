from src.services import fleet_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_staging_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "get_hub_supply", lambda tenant_id, hub_id: {"executive": 0})
    monkeypatch.setattr(
        fleet_tools, "propose_transfer",
        lambda tenant_id, vehicle_id, to_hub_id, reason, agent_key: {"id": "transfer-1", "status": "proposed"},
    )
    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-staging",
        json={
            "event_type": "subscription.created", "vehicle_id": "veh-1", "destination_hub_id": "hub-1",
            "context": {"vehicle_class": "executive", "supply_threshold": 1},
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["proposed_transfer"] == {"id": "transfer-1", "status": "proposed"}


async def test_evaluate_safety_score_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_safety_score", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-safety-score",
        json={"vehicle_id": "veh-1", "event_counts": {"speeding": 1}, "window_days": 30},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["score"] == 90.0


async def test_evaluate_intake_readiness_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_intake_readiness", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-intake-readiness",
        json={"intake_inspection_id": "insp-1", "passed": True, "notes": "minor scuff on rear bumper"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recommendation"] == "provision_with_flag"


async def test_evaluate_telemetry_anomaly_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_telemetry_anomaly", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-telemetry-anomaly",
        json={"geofence_alert_id": "alert-1", "distance_miles": 200.0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["assessment"] == "needs_investigation"


async def test_evaluate_maintenance_triage_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_maintenance_triage", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-maintenance-triage",
        json={"alerts": [{"alert_id": "a1", "vehicle_id": "veh-1", "severity": "urgent", "dtc_codes": ["P0300"]}]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["priority_order"] == ["veh-1"]


async def test_evaluate_incident_severity_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_incident_severity", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-incident-severity",
        json={"incident_id": "inc-1", "incident_type": "accident", "description": "airbag deployed"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["severity"] == "severe"


async def test_evaluate_valuation_anomaly_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_valuation_anomaly", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-valuation-anomaly",
        json={"vehicle_id": "veh-1", "asset_valuation_id": "val-1", "book_value_usd": 50000.0, "market_value_usd": 30000.0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["assessment"] == "flagged"


async def test_evaluate_disposition_channel_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "create_disposition_channel_recommendation", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-disposition-channel",
        json={"vehicle_id": "veh-1", "odometer_miles": 65000, "vehicle_class": "urban"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recommended_channel"] == "rideshare"


async def test_evaluate_covenant_risk_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_covenant_risk", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-covenant-risk",
        json={"facility_id": "fac-1", "utilization_pct": 95.0, "max_utilization_pct": 90.0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["risk_level"] == "breached"


async def test_evaluate_fuel_anomaly_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fuel_anomaly", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-fuel-anomaly",
        json={"vehicle_id": "veh-1", "fuel_log_entry_id": "entry-1", "energy_type": "gasoline", "implied_efficiency": 95.0, "cost_per_unit_usd": 3.50},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["assessment"] == "flagged"


async def test_evaluate_battery_degradation_trend_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_battery_degradation_forecast", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-battery-degradation-trend",
        json={"vehicle_id": "veh-1", "first_soh_pct": 95.0, "latest_soh_pct": 92.0, "days_elapsed": 60.0, "threshold_pct": 80.0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["estimated_days_to_threshold"] == 240


async def test_evaluate_fleet_optimization_endpoint(client, monkeypatch):
    monkeypatch.setattr(fleet_tools, "record_fleet_optimization", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-fleet-optimization",
        json={"vehicle_id": "veh-1", "total_tco_usd": 30000.0, "availability_pct": 60.0, "is_disposition_eligible": True, "valuation_flagged": True},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["recommendation"] == "prioritize_disposition"
