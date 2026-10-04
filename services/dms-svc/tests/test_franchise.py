from tests.conftest import TENANT_HEADERS


async def _rooftop(client):
    resp = await client.post("/api/v1/dms/rooftops", json={"name": "Austin Toyota", "oem_brand": "Toyota", "license_number": "L1", "license_expiry": "2027-01-01T00:00:00Z"}, headers=TENANT_HEADERS)
    return resp.json()["id"]


async def test_non_compliant_rooftop_auto_remediated(client):
    rooftop_id = await _rooftop(client)
    await client.post("/api/v1/dms/franchise", json={
        "rooftop_id": rooftop_id, "status": "non_compliant", "score": 61, "notes": "signage program lapsed",
    }, headers=TENANT_HEADERS)
    resp = await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)
    remediated = resp.json()
    assert len(remediated) == 1
    assert remediated[0]["remediation_opened"] is True


async def test_compliant_rooftop_not_remediated(client):
    rooftop_id = await _rooftop(client)
    await client.post("/api/v1/dms/franchise", json={"rooftop_id": rooftop_id, "status": "compliant", "score": 94}, headers=TENANT_HEADERS)
    resp = await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)
    assert resp.json() == []


async def test_remediation_does_not_reopen_once_applied(client):
    rooftop_id = await _rooftop(client)
    await client.post("/api/v1/dms/franchise", json={"rooftop_id": rooftop_id, "status": "non_compliant", "score": 55}, headers=TENANT_HEADERS)
    first = (await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)).json()
    assert len(first) == 1
    second = (await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)).json()
    assert second == []
