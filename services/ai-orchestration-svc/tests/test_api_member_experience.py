from tests.conftest import TENANT_HEADERS


async def test_evaluate_churn_risk_endpoint(client, monkeypatch):
    from src.services import subscription_tools
    monkeypatch.setattr(subscription_tools, "award_retention_gu_bonus", lambda tenant_id, member_profile_id, amount: {})

    resp = await client.post(
        "/api/v1/ai/member-experience/evaluate-churn-risk",
        json={"member_profile_id": "mem-1", "context": {"days_since_last_use": 45, "pause_count_last_year": 2}},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["risk_score"] is not None


async def test_evaluate_winback_endpoint(client):
    resp = await client.post(
        "/api/v1/ai/member-experience/evaluate-winback",
        json={"member_profile_id": "mem-1", "subscription_id": "sub-1", "reason": "too expensive"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["offer"] is not None
