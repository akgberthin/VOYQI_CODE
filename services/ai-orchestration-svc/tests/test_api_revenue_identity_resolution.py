from src.services import crm_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_identity_resolution_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "get_person_profiles",
        lambda tenant_id: {"profiles": [{"id": "profile-1", "full_name": "Priya Sharma", "primary_email": "priya@example.com", "primary_phone": ""}]},
    )
    monkeypatch.setattr(crm_tools, "create_match_candidate", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/crm/evaluate-identity-resolution",
        json={"source_record_id": "record-1", "full_name": "Priya Sharma", "email": "priya@example.com"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_person_profile_id"] == "profile-1"
    assert resp.json()["match_score"] == 0.95


async def test_evaluate_lead_scoring_endpoint(client, monkeypatch):
    monkeypatch.setattr(crm_tools, "check_duplicate_leads", lambda tenant_id, email, phone: {"duplicates": []})
    monkeypatch.setattr(crm_tools, "update_lead_score", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/crm/evaluate-lead-scoring",
        json={"lead_id": "lead-1", "source": "dealer", "full_name": "Jane Doe", "email": "jane@acmecorp.com", "phone": "305-555-0199"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["score"] == 1.0
    assert resp.json()["routed_to_team"] == "enterprise_sales"


async def test_evaluate_profile_deduplication_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        crm_tools, "get_active_person_profiles",
        lambda tenant_id, exclude_profile_id="": {"profiles": [{"id": "profile-1", "full_name": "Priya Sharma", "primary_email": "priya@example.com", "primary_phone": ""}]},
    )
    monkeypatch.setattr(crm_tools, "create_profile_duplicate_candidate", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/crm/evaluate-profile-deduplication",
        json={"person_profile_id": "profile-2", "full_name": "P. Sharma", "email": "priya@example.com"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_profile_id"] == "profile-1"
    assert resp.json()["match_score"] == 0.95
