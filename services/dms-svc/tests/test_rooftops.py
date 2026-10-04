from tests.conftest import TENANT_HEADERS


async def test_create_and_list_rooftop(client):
    resp = await client.post("/api/v1/dms/rooftops", json={
        "name": "Austin Toyota", "oem_brand": "Toyota", "license_number": "TX-1",
        "license_expiry": "2027-01-01T00:00:00Z",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["renewal_filed"] is False

    list_resp = await client.get("/api/v1/dms/rooftops", headers=TENANT_HEADERS)
    assert len(list_resp.json()) == 1


async def test_evaluate_renewals_files_for_near_expiry_rooftop(client):
    from datetime import datetime, timedelta, timezone
    near_expiry = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    far_expiry = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()

    await client.post("/api/v1/dms/rooftops", json={"name": "Near", "oem_brand": "Toyota", "license_number": "N1", "license_expiry": near_expiry}, headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/rooftops", json={"name": "Far", "oem_brand": "BMW", "license_number": "F1", "license_expiry": far_expiry}, headers=TENANT_HEADERS)

    resp = await client.post("/api/v1/dms/rooftops/evaluate-renewals", headers=TENANT_HEADERS)
    filed = resp.json()
    assert len(filed) == 1
    assert filed[0]["name"] == "Near"

    # Re-evaluating doesn't file it again (already filed).
    resp2 = await client.post("/api/v1/dms/rooftops/evaluate-renewals", headers=TENANT_HEADERS)
    assert resp2.json() == []
