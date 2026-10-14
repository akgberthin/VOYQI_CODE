import pytest

from tests.conftest import TENANT_HEADERS

TECH_ENTITY = "VOYQI Technology LLC"


async def _register_and_grant_patent(client, name="Patent"):
    reg_resp = await client.post(
        "/api/v1/ip/assets",
        json={"asset_type": "patent", "name": name, "owning_entity": TECH_ENTITY, "jurisdiction": "US"},
        headers=TENANT_HEADERS,
    )
    asset_id = reg_resp.json()["id"]
    await client.post(f"/api/v1/ip/assets/{asset_id}/status", json={"new_status": "granted", "registration_number": "US1"}, headers=TENANT_HEADERS)
    return asset_id


@pytest.mark.asyncio
async def test_register_asset_via_api(client):
    resp = await client.post("/api/v1/ip/assets", json={"asset_type": "trade_secret", "name": "Routing algorithm", "owning_entity": TECH_ENTITY}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "protected"


@pytest.mark.asyncio
async def test_invalid_status_transition_returns_422(client):
    resp = await client.post("/api/v1/ip/assets", json={"asset_type": "patent", "name": "Patent", "owning_entity": TECH_ENTITY, "jurisdiction": "US"}, headers=TENANT_HEADERS)
    asset_id = resp.json()["id"]

    bad = await client.post(f"/api/v1/ip/assets/{asset_id}/status", json={"new_status": "compromised"}, headers=TENANT_HEADERS)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_license_flow_via_api(client):
    asset_id = await _register_and_grant_patent(client)

    license_resp = await client.post(
        "/api/v1/ip/licenses",
        json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Fleet LLC", "license_type": "non_exclusive", "royalty_type": "percentage_of_revenue", "royalty_rate": 500},
        headers=TENANT_HEADERS,
    )
    assert license_resp.status_code == 201
    assert license_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_exclusive_conflict_returns_409(client):
    asset_id = await _register_and_grant_patent(client)
    await client.post("/api/v1/ip/licenses", json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Fleet LLC", "license_type": "exclusive"}, headers=TENANT_HEADERS)

    conflict = await client.post("/api/v1/ip/licenses", json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Mobility LLC", "license_type": "non_exclusive"}, headers=TENANT_HEADERS)
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_licensing_a_pending_asset_returns_409(client):
    reg_resp = await client.post("/api/v1/ip/assets", json={"asset_type": "patent", "name": "Still pending", "owning_entity": TECH_ENTITY, "jurisdiction": "US"}, headers=TENANT_HEADERS)
    asset_id = reg_resp.json()["id"]

    resp = await client.post("/api/v1/ip/licenses", json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Fleet LLC", "license_type": "non_exclusive"}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_full_royalty_lifecycle_via_api(client):
    asset_id = await _register_and_grant_patent(client)
    license_resp = await client.post(
        "/api/v1/ip/licenses",
        json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Fleet LLC", "license_type": "non_exclusive", "royalty_type": "percentage_of_revenue", "royalty_rate": 1000},
        headers=TENANT_HEADERS,
    )
    license_id = license_resp.json()["id"]

    statement_resp = await client.post(
        "/api/v1/ip/royalty-statements",
        json={"license_agreement_id": license_id, "period_start": "2026-01-01T00:00:00Z", "period_end": "2026-01-31T00:00:00Z", "licensee_reported_revenue_cents": 1000000},
        headers=TENANT_HEADERS,
    )
    assert statement_resp.status_code == 201
    statement_id = statement_resp.json()["id"]
    assert statement_resp.json()["royalty_owed_cents"] == 100000  # 10% of $10,000

    finalize_resp = await client.post(f"/api/v1/ip/royalty-statements/{statement_id}/finalize", headers=TENANT_HEADERS)
    assert finalize_resp.status_code == 200
    assert finalize_resp.json()["status"] == "finalized"

    pay_resp = await client.post(f"/api/v1/ip/royalty-statements/{statement_id}/pay", headers=TENANT_HEADERS)
    assert pay_resp.status_code == 200
    assert pay_resp.json()["status"] == "paid"


@pytest.mark.asyncio
async def test_pay_before_finalize_returns_409(client):
    asset_id = await _register_and_grant_patent(client)
    license_resp = await client.post("/api/v1/ip/licenses", json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Fleet LLC", "license_type": "non_exclusive", "royalty_type": "fixed_fee", "royalty_rate": 5000}, headers=TENANT_HEADERS)
    license_id = license_resp.json()["id"]
    statement_resp = await client.post(
        "/api/v1/ip/royalty-statements",
        json={"license_agreement_id": license_id, "period_start": "2026-01-01T00:00:00Z", "period_end": "2026-01-31T00:00:00Z"},
        headers=TENANT_HEADERS,
    )
    statement_id = statement_resp.json()["id"]

    resp = await client.post(f"/api/v1/ip/royalty-statements/{statement_id}/pay", headers=TENANT_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_list_assets_by_owning_entity_via_api(client):
    await client.post("/api/v1/ip/assets", json={"asset_type": "trade_secret", "name": "Secret 1", "owning_entity": TECH_ENTITY}, headers=TENANT_HEADERS)
    await client.post("/api/v1/ip/assets", json={"asset_type": "trade_secret", "name": "Secret 2", "owning_entity": TECH_ENTITY}, headers=TENANT_HEADERS)

    resp = await client.get("/api/v1/ip/assets", params={"owning_entity": TECH_ENTITY}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_ip_lifecycle_events_are_json_serializable(client, monkeypatch):
    """The serialized asset/license carries UUIDs and dates — the
    publisher must encode them rather than silently drop the event."""
    import json

    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        json.dumps(payload, default=str)
        events.append(event_type)

    monkeypatch.setattr("src.api.ip_assets.publish_event", _spy)
    asset_id = await _register_and_grant_patent(client)
    await client.post(
        "/api/v1/ip/licenses",
        json={"ip_asset_id": asset_id, "licensee_entity": "VOYQI Fleet LLC", "license_type": "non_exclusive", "royalty_type": "percentage_of_revenue", "royalty_rate": 500},
        headers=TENANT_HEADERS,
    )

    assert events == ["ip.asset_registered", "ip.asset_status_changed", "ip.license_created"]
