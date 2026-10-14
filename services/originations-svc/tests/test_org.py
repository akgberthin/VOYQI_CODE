"""ORG-001..003: case -> prequalification -> decisioning -> tier-3
finalization -> funding match, with the events each stage publishes."""
import pytest

CASE = {"origination_type": "subscription_enrollment", "full_name": "Jane Doe", "amount_usd": 12000.0, "external_ref_id": "lead-42"}


@pytest.fixture
def published(monkeypatch):
    events = []

    async def _spy(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("src.services.org_engine.publish_event", _spy)
    return events


def _types(published):
    return [event_type for event_type, _payload in published]


async def _case(client, tenant_id, **overrides):
    resp = await client.post("/api/v1/org/cases", json={**CASE, **overrides}, headers={"X-Tenant-Id": tenant_id})
    assert resp.status_code == 201
    return resp.json()


async def _scored_decision(client, tenant_id, risk_score):
    h = {"X-Tenant-Id": tenant_id}
    case = await _case(client, tenant_id)
    decision = (await client.post(f"/api/v1/org/cases/{case['id']}/decisions", headers=h)).json()
    await client.post(f"/api/v1/org/decisions/{decision['id']}/apply-score", json={"risk_score": risk_score, "reasoning": "r"}, headers=h)
    return case, decision


async def test_create_case_publishes_case_created(client, tenant_id, published):
    case = await _case(client, tenant_id)
    assert case["status"] == "open"
    [(event_type, payload)] = published
    assert event_type == "org.case_created"
    assert payload == {
        "tenant_id": tenant_id, "case_id": case["id"], "origination_type": "subscription_enrollment",
        "full_name": "Jane Doe", "external_ref_id": "lead-42",
    }


@pytest.mark.parametrize("verdict,status", [("qualified", "prequalified"), ("needs_review", "needs_review"), ("incomplete", "incomplete"), ("weird", "needs_review")])
async def test_prequalification_write_back_sets_status(client, tenant_id, published, verdict, status):
    case = await _case(client, tenant_id)
    resp = await client.post(
        f"/api/v1/org/cases/{case['id']}/prequalification",
        json={"verdict": verdict, "reasoning": "lead score 0.8", "next_step_guidance": "proceed"},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == status
    assert resp.json()["prequal_verdict"] == verdict


async def test_request_decisioning_publishes_event_and_blocks_duplicates(client, tenant_id, published):
    h = {"X-Tenant-Id": tenant_id}
    case = await _case(client, tenant_id)
    resp = await client.post(f"/api/v1/org/cases/{case['id']}/decisions", headers=h)
    assert resp.status_code == 201
    assert resp.json()["status"] == "awaiting_score"
    assert _types(published) == ["org.case_created", "org.decisioning_requested"]
    assert published[1][1]["decision_id"] == resp.json()["id"]

    again = await client.post(f"/api/v1/org/cases/{case['id']}/decisions", headers=h)
    assert again.status_code == 409


@pytest.mark.parametrize("score,outcome", [(0.85, "approve"), (0.7, "approve"), (0.55, "manual_review"), (0.39, "decline")])
async def test_apply_score_recommends_by_fixed_threshold(client, tenant_id, published, score, outcome):
    _case_, decision = await _scored_decision(client, tenant_id, score)
    resp = await client.get(f"/api/v1/org/decisions/{decision['id']}", headers={"X-Tenant-Id": tenant_id})
    assert resp.json()["status"] == "scored"
    assert resp.json()["recommended_outcome"] == outcome


async def test_apply_score_rejects_out_of_range(client, tenant_id, published):
    h = {"X-Tenant-Id": tenant_id}
    case = await _case(client, tenant_id)
    decision = (await client.post(f"/api/v1/org/cases/{case['id']}/decisions", headers=h)).json()
    resp = await client.post(f"/api/v1/org/decisions/{decision['id']}/apply-score", json={"risk_score": 1.5, "reasoning": "r"}, headers=h)
    assert resp.status_code == 422


@pytest.mark.parametrize("approve", [True, False])
async def test_finalize_without_accountable_role_is_hard_blocked(client, tenant_id, published, approve):
    _case_, decision = await _scored_decision(client, tenant_id, 0.9)
    resp = await client.post(
        f"/api/v1/org/decisions/{decision['id']}/finalize", json={"approve": approve, "accountable_role": "  "}, headers={"X-Tenant-Id": tenant_id}
    )
    assert resp.status_code == 400
    assert "accountable_role" in resp.json()["detail"]
    assert "org.decision_finalized" not in _types(published)


async def test_finalize_before_scoring_is_blocked(client, tenant_id, published):
    h = {"X-Tenant-Id": tenant_id}
    case = await _case(client, tenant_id)
    decision = (await client.post(f"/api/v1/org/cases/{case['id']}/decisions", headers=h)).json()
    resp = await client.post(f"/api/v1/org/decisions/{decision['id']}/finalize", json={"approve": True, "accountable_role": "senior_underwriter"}, headers=h)
    assert resp.status_code == 400


async def test_approve_publishes_finalized_and_funding_match(client, tenant_id, published):
    h = {"X-Tenant-Id": tenant_id}
    case, decision = await _scored_decision(client, tenant_id, 0.82)
    resp = await client.post(
        f"/api/v1/org/decisions/{decision['id']}/finalize",
        json={"approve": True, "accountable_role": "senior_underwriter", "decided_by": "u-7", "reasoning": "clean file"},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["accountable_role"] == "senior_underwriter"

    finalized = dict(published)["org.decision_finalized"]
    assert finalized["outcome"] == "approved"
    assert finalized["followed_recommendation"] is True
    funding = dict(published)["org.funding_match_requested"]
    assert funding == {
        "tenant_id": tenant_id, "case_id": case["id"], "decision_id": decision["id"],
        "origination_type": "subscription_enrollment", "risk_score": 0.82, "amount_usd": 12000.0,
    }
    case_now = (await client.get(f"/api/v1/org/cases/{case['id']}", headers=h)).json()
    assert case_now["status"] == "approved"

    again = await client.post(f"/api/v1/org/decisions/{decision['id']}/finalize", json={"approve": False, "accountable_role": "x"}, headers=h)
    assert again.status_code == 409


async def test_decline_publishes_finalized_but_no_funding_match(client, tenant_id, published):
    _case_, decision = await _scored_decision(client, tenant_id, 0.2)
    await client.post(
        f"/api/v1/org/decisions/{decision['id']}/finalize", json={"approve": False, "accountable_role": "senior_underwriter"}, headers={"X-Tenant-Id": tenant_id}
    )
    types = _types(published)
    assert "org.decision_finalized" in types
    assert "org.funding_match_requested" not in types


async def test_disparate_impact_snapshot_groups_by_origination_type(client, tenant_id, published):
    h = {"X-Tenant-Id": tenant_id}
    for approve in (True, False, False):
        _case_, decision = await _scored_decision(client, tenant_id, 0.5)
        await client.post(f"/api/v1/org/decisions/{decision['id']}/finalize", json={"approve": approve, "accountable_role": "uw"}, headers=h)

    snapshot = (await client.get("/api/v1/org/compliance/disparate-impact-snapshot", headers=h)).json()
    assert snapshot == {
        "groups": [{"origination_type": "subscription_enrollment", "total": 3, "approved": 1, "declined": 2, "decline_rate_pct": 66.7}]
    }


async def test_funding_sources_filter_by_type_with_agent_contract_fields(client, tenant_id):
    h = {"X-Tenant-Id": tenant_id}
    await client.post(
        "/api/v1/org/funding-sources",
        json={"name": "Captive Finance", "applicable_origination_type": "subscription_enrollment", "min_risk_score": 0.6, "max_amount_usd": 50000, "reserve_participation_pct": 2.5},
        headers=h,
    )
    await client.post(
        "/api/v1/org/funding-sources",
        json={"name": "Rental Bank", "applicable_origination_type": "rental_conversion", "min_risk_score": 0.5, "max_amount_usd": 20000},
        headers=h,
    )
    sources = (await client.get("/api/v1/org/funding-sources", params={"origination_type": "subscription_enrollment"}, headers=h)).json()
    assert [s["name"] for s in sources] == ["Captive Finance"]
    # Exactly the keys revenue_agent._deterministic_funding_match reads.
    for key in ("id", "applicable_origination_type", "min_risk_score", "max_amount_usd", "reserve_participation_pct", "name"):
        assert key in sources[0]


async def test_org_is_tenant_scoped(client, published):
    case = await _case(client, "tenant-a")
    resp = await client.get(f"/api/v1/org/cases/{case['id']}", headers={"X-Tenant-Id": "tenant-b"})
    assert resp.status_code == 404
