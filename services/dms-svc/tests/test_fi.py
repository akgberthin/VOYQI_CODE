import uuid

from tests.conftest import TENANT_HEADERS


async def test_adverse_action_issued_immediately_no_pending_state(client):
    resp = await client.post("/api/v1/dms/fi/adverse-action-notices", json={
        "deal_id": str(uuid.uuid4()), "reason": "credit criteria not met",
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "issued"  # never left "required"/"overdue" pending


async def test_red_flag_override_requires_nonempty_human_typed_reason(client):
    """The one endpoint that structurally refuses to proceed without a
    real reason — no default, no bypass, security matter."""
    check = (await client.post("/api/v1/dms/fi/red-flag-checks", json={
        "deal_id": str(uuid.uuid4()), "flags": ["address mismatch", "SSN issued after stated DOB"],
    }, headers=TENANT_HEADERS)).json()

    empty_resp = await client.post(f"/api/v1/dms/fi/red-flag-checks/{check['id']}/override", json={
        "overridden_by": "fi-manager-1", "reason": "",
    }, headers=TENANT_HEADERS)
    assert empty_resp.status_code == 422

    whitespace_resp = await client.post(f"/api/v1/dms/fi/red-flag-checks/{check['id']}/override", json={
        "overridden_by": "fi-manager-1", "reason": "   ",
    }, headers=TENANT_HEADERS)
    assert whitespace_resp.status_code == 422

    real_resp = await client.post(f"/api/v1/dms/fi/red-flag-checks/{check['id']}/override", json={
        "overridden_by": "fi-manager-1", "reason": "verified in person with government ID",
    }, headers=TENANT_HEADERS)
    assert real_resp.status_code == 200
    assert real_resp.json()["overridden_by"] == "fi-manager-1"


async def test_red_flag_check_with_no_flags_has_no_override_needed(client):
    check = (await client.post("/api/v1/dms/fi/red-flag-checks", json={
        "deal_id": str(uuid.uuid4()), "flags": [],
    }, headers=TENANT_HEADERS)).json()
    assert check["flags"] == []
    assert check["overridden_by"] is None
