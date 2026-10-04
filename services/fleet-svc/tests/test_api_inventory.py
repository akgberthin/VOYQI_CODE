from tests.conftest import TENANT_HEADERS


async def test_full_intake_flow_via_api(client):
    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders",
        json={"source": "dealer", "vehicle_description": "2024 BMW M4", "cost_usd": 78000.0, "created_by": "fleet-ops-1"},
        headers=TENANT_HEADERS,
    )
    assert po_resp.status_code == 201
    po_id = po_resp.json()["id"]

    receive_resp = await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/receive",
        json={"vin": "WBA3B1C50EK116345", "make": "BMW", "model": "M4", "year": 2024, "vehicle_class": "performance"},
        headers=TENANT_HEADERS,
    )
    assert receive_resp.status_code == 200
    vehicle_id = receive_resp.json()["id"]

    inspect_resp = await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/inspect",
        json={"checklist": {"tires": "pass"}, "passed": True, "inspected_by": "fleet-ops-1"},
        headers=TENANT_HEADERS,
    )
    assert inspect_resp.json()["passed"] is True

    provision_resp = await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/provision",
        json={"provisioned_by": "fleet-ops-1", "card_id": "VQ-2001"},
        headers=TENANT_HEADERS,
    )
    assert provision_resp.json()["tls_cert_common_name"] == "WBA3B1C50EK116345"

    certify_resp = await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/certify-class",
        json={"certified_by": "fleet-ops-1"},
        headers=TENANT_HEADERS,
    )
    assert certify_resp.json()["status"] == "staged"

    status_resp = await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/status", json={"status": "assigned"}, headers=TENANT_HEADERS
    )
    assert status_resp.json()["status"] == "assigned"


async def test_certify_without_provisioning_409(client):
    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders",
        json={"source": "auction", "vehicle_description": "Honda Civic", "cost_usd": 22000.0, "created_by": "fleet-ops-1"},
        headers=TENANT_HEADERS,
    )
    po_id = po_resp.json()["id"]
    receive_resp = await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/receive",
        json={"vin": "2HGFC2F59NH500001", "make": "Honda", "model": "Civic", "year": 2024, "vehicle_class": "urban"},
        headers=TENANT_HEADERS,
    )
    vehicle_id = receive_resp.json()["id"]
    resp = await client.post(
        f"/api/v1/fleet/vehicles/{vehicle_id}/certify-class", json={"certified_by": "x"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 409


async def test_get_unknown_vehicle_404(client):
    import uuid

    resp = await client.get(f"/api/v1/fleet/vehicles/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_inspection_publishes_readiness_event(client, monkeypatch):
    published = []

    def spy(topic, event_type, tenant_id, payload):
        published.append((event_type, payload))

    from src.api import inventory
    monkeypatch.setattr(inventory, "publish_event", spy)

    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders", json={"source": "dealer", "vehicle_description": "2024 BMW M4", "cost_usd": 78000.0, "created_by": "fleet-ops-1"}, headers=TENANT_HEADERS
    )
    po_id = po_resp.json()["id"]
    await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/receive",
        json={"vin": "WBA3B1C50EK116346", "make": "BMW", "model": "M4", "year": 2024, "vehicle_class": "performance"},
        headers=TENANT_HEADERS,
    )
    inspect_resp = await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/inspect", json={"checklist": {"tires": "pass"}, "passed": True, "inspected_by": "fleet-ops-1", "notes": "minor scuff on rear bumper"}, headers=TENANT_HEADERS
    )
    assert inspect_resp.status_code == 200
    event_type, payload = published[0]
    assert event_type == "flt.intake_readiness_requested"
    assert payload["notes"] == "minor scuff on rear bumper"


async def test_assess_readiness_now_endpoint(client, monkeypatch):
    from src.services import ai_orchestration_tools

    monkeypatch.setattr(ai_orchestration_tools, "request_intake_readiness", lambda tenant_id, inspection_id, passed, notes: {"assessment_created": True, "reasoning": "x"})

    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders", json={"source": "dealer", "vehicle_description": "2024 BMW M4", "cost_usd": 78000.0, "created_by": "fleet-ops-1"}, headers=TENANT_HEADERS
    )
    po_id = po_resp.json()["id"]
    await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/receive", json={"vin": "WBA3B1C50EK116347", "make": "BMW", "model": "M4", "year": 2024, "vehicle_class": "performance"}, headers=TENANT_HEADERS
    )
    inspect_resp = await client.post(f"/api/v1/fleet/purchase-orders/{po_id}/inspect", json={"checklist": {}, "passed": True, "inspected_by": "fleet-ops-1"}, headers=TENANT_HEADERS)

    resp = await client.post(f"/api/v1/fleet/intake-inspections/{inspect_resp.json()['id']}/assess-readiness-now", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["assessment_created"] is True


async def test_get_readiness_not_found_returns_404(client):
    import uuid
    resp = await client.get(f"/api/v1/fleet/intake-inspections/{uuid.uuid4()}/readiness", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_create_readiness_assessment_endpoint(client):
    po_resp = await client.post(
        "/api/v1/fleet/purchase-orders", json={"source": "dealer", "vehicle_description": "2024 BMW M4", "cost_usd": 78000.0, "created_by": "fleet-ops-1"}, headers=TENANT_HEADERS
    )
    po_id = po_resp.json()["id"]
    await client.post(
        f"/api/v1/fleet/purchase-orders/{po_id}/receive", json={"vin": "WBA3B1C50EK116348", "make": "BMW", "model": "M4", "year": 2024, "vehicle_class": "performance"}, headers=TENANT_HEADERS
    )
    inspect_resp = await client.post(f"/api/v1/fleet/purchase-orders/{po_id}/inspect", json={"checklist": {}, "passed": True, "inspected_by": "fleet-ops-1"}, headers=TENANT_HEADERS)

    resp = await client.post(
        f"/api/v1/fleet/intake-inspections/{inspect_resp.json()['id']}/readiness-assessments", json={"recommendation": "provision_with_flag", "reasoning": "minor scuff noted"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["recommendation"] == "provision_with_flag"

    get_resp = await client.get(f"/api/v1/fleet/intake-inspections/{inspect_resp.json()['id']}/readiness", headers=TENANT_HEADERS)
    assert get_resp.json()["recommendation"] == "provision_with_flag"
