from src.services import crm_tools, org_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_prequalification_endpoint(client, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": {"score": 0.85, "routed_to_team": "enterprise_sales"}})
    monkeypatch.setattr(org_tools, "set_case_prequalification", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/org/evaluate-prequalification",
        json={"case_id": "case-1", "origination_type": "retail_finance", "full_name": "Priya Sharma", "external_ref_id": "lead-1"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "qualified"


async def test_evaluate_risk_scoring_endpoint(client, monkeypatch):
    monkeypatch.setattr(crm_tools, "get_lead_score", lambda tenant_id, external_ref_id: {"lead": {"score": 0.78, "routed_to_team": "member_experience"}})
    monkeypatch.setattr(org_tools, "apply_case_risk_score", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/org/evaluate-risk-scoring",
        json={"decision_id": "decision-1", "case_id": "case-1", "origination_type": "subscription_enrollment", "full_name": "Jane Doe", "external_ref_id": "lead-1"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["risk_score"] == 0.78


async def test_evaluate_funding_match_endpoint(client, monkeypatch):
    from src.services import org_tools

    monkeypatch.setattr(
        org_tools, "get_funding_sources",
        lambda tenant_id, origination_type: {"sources": [{"id": "source-1", "name": "Lender A", "applicable_origination_type": "retail_finance", "min_risk_score": 0.3, "max_amount_usd": 90000.0, "reserve_participation_pct": 3.0}]},
    )
    resp = await client.post(
        "/api/v1/ai/org/evaluate-funding-match",
        json={"case_id": "case-1", "origination_type": "retail_finance", "risk_score": 0.6, "amount_usd": 40000.0},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_funding_source_id"] == "source-1"
