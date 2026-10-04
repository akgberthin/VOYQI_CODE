from src.services import compliance_audit_agent, dealer_intelligence_agent, dms_tools, identity_tools, rbac_tools
from tests.conftest import TENANT_HEADERS


class _UnconfiguredClient:
    is_configured = False


async def _create_pending_recommendation(db_session):
    payload = {
        "override_id": "ov-100",
        "user_id": "user-100",
        "permission_key": "rbac.role.grant_admin",
        "effect": "allow",
        "is_sensitive": True,
        "granted_by": "ai-orchestration-svc",
        "ai_initiated": True,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, "voyqi-holdings", "permission.granted", payload, client=_UnconfiguredClient()
    )
    return evaluation.recommendation


async def test_list_recommendations_empty(client):
    resp = await client.get("/api/v1/ai/recommendations", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_approve_recommendation_executes_revoke(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        rbac_tools,
        "revoke_permission_override",
        lambda tenant_id, override_id, reason: calls.append((tenant_id, override_id, reason))
        or {"status": "revoked"},
    )

    rec = await _create_pending_recommendation(db_session)
    assert rec is not None

    resp = await client.post(
        f"/api/v1/ai/recommendations/{rec.id}/approve",
        json={"reason_code": "confirmed_unwarranted", "note": "verified with security team"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert len(calls) == 1
    assert calls[0][1] == "ov-100"


async def test_reject_recommendation(client, db_session):
    rec = await _create_pending_recommendation(db_session)
    resp = await client.post(
        f"/api/v1/ai/recommendations/{rec.id}/reject",
        json={"reason_code": "false_positive", "note": "grant was pre-approved by security"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


async def test_cannot_decide_recommendation_twice(client, db_session):
    rec = await _create_pending_recommendation(db_session)
    await client.post(
        f"/api/v1/ai/recommendations/{rec.id}/reject",
        json={"reason_code": "false_positive"},
        headers=TENANT_HEADERS,
    )
    resp = await client.post(
        f"/api/v1/ai/recommendations/{rec.id}/approve",
        json={"reason_code": "too_late"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409


async def test_approve_break_glass_recommendation_ends_session(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        identity_tools,
        "end_break_glass_session",
        lambda tenant_id, session_id, reason: calls.append((tenant_id, session_id, reason))
        or {"ended": True},
    )
    payload = {
        "session_id": "bg-99", "user_id": "user-99", "activated_by": "garth",
        "duration_minutes": 20, "off_hours": True,
    }
    evaluation = await compliance_audit_agent.evaluate_event(
        db_session, "voyqi-holdings", "access.break_glass_activated", payload, client=_UnconfiguredClient()
    )
    rec = evaluation.recommendation
    assert rec is not None

    resp = await client.post(
        f"/api/v1/ai/recommendations/{rec.id}/approve",
        json={"reason_code": "confirmed_unnecessary", "note": "verified incident already resolved"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0][1] == "bg-99"


async def test_approve_tier_3_credit_decision_without_accountable_role_is_blocked(client, db_session, monkeypatch):
    monkeypatch.setattr(
        dms_tools, "pull_bureau_and_route",
        lambda tenant_id, app_id, bureau, fico, ofac_match: {"status": "decisioned", "selected_submission_id": "sub-1"},
    )
    result = await dealer_intelligence_agent.evaluate_lending(db_session, "voyqi-holdings", "app-1", "experian", 780, False)
    rec_id = result.decision_log.recommendation_id

    resp = await client.post(
        f"/api/v1/ai/recommendations/{rec_id}/approve",
        json={"reason_code": "credit_approved"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 400
    assert "accountable_role" in resp.json()["detail"]


async def test_approve_tier_3_credit_decision_with_accountable_role_executes_it(client, db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        dms_tools, "pull_bureau_and_route",
        lambda tenant_id, app_id, bureau, fico, ofac_match: calls.append((app_id, bureau, fico, ofac_match))
        or {"status": "decisioned", "selected_submission_id": "sub-1"},
    )
    result = await dealer_intelligence_agent.evaluate_lending(db_session, "voyqi-holdings", "app-1", "experian", 780, False)
    rec_id = result.decision_log.recommendation_id

    resp = await client.post(
        f"/api/v1/ai/recommendations/{rec_id}/approve",
        json={
            "reason_code": "credit_approved",
            "note": "verified income docs",
            "accountable_role": "senior_finance_manager",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["accountable_role"] == "senior_finance_manager"
    assert len(calls) == 1
    assert calls[0] == ("app-1", "experian", 780, False)


async def test_decide_unknown_recommendation_404(client):
    import uuid

    resp = await client.post(
        f"/api/v1/ai/recommendations/{uuid.uuid4()}/reject",
        json={"reason_code": "n/a"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
