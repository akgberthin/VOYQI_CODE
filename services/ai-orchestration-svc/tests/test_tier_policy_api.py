"""AI-005: tier changes per tenant, with the tiering instructions' rules
enforced in code."""
from datetime import datetime, timezone

import pytest

from src.core import prompts, tier_overrides, tiering
from src.models import AIRecommendation, RecommendationStatus
from tests.conftest import TENANT_HEADERS

TENANT = TENANT_HEADERS["X-Tenant-Id"]
URL = "/api/v1/ai/tier-policy"


@pytest.fixture(autouse=True)
def _clean():
    tiering._overrides.clear()
    yield
    tiering._overrides.clear()


async def _decisions(db, action, approved, rejected, policy="talent-workforce-intelligence"):
    for status, count in ((RecommendationStatus.APPROVED, approved), (RecommendationStatus.REJECTED, rejected)):
        for _ in range(count):
            db.add(AIRecommendation(
                tenant_id=TENANT, agent_key="communication_agent", domain="hrm", action_type=action,
                proposed_action={"executor": "hrm.send_email", "args": {}, "policy": f"{policy}.{action}"},
                reasoning="r", status=status, triggering_event_type="e", tier=2, created_at=datetime.now(timezone.utc),
            ))
    await db.commit()


@pytest.mark.parametrize("agent,action", [
    ("sales-originations-intelligence", "credit.decision"),
    ("compliance-risk-intelligence", "abac.policy_grant_apply"),
    ("fleet-intelligence", "telematics.tamper_event_flag"),
])
async def test_tier_3_actions_cannot_be_changed(client, agent, action):
    resp = await client.put(f"{URL}/{agent}/{action}", json={"tier": 2, "reason": "faster"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422
    assert "tier 3" in resp.json()["detail"]


async def test_nothing_can_be_raised_into_tier_3_here(client):
    resp = await client.put(f"{URL}/talent-workforce-intelligence/email.send", json={"tier": 3, "reason": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


async def test_unknown_actions_are_refused(client):
    resp = await client.put(f"{URL}/talent-workforce-intelligence/made.up", json={"tier": 2, "reason": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


async def test_making_an_action_stricter_needs_no_evidence_and_takes_effect(client):
    resp = await client.put(f"{URL}/talent-workforce-intelligence/email.send", json={"tier": 2, "reason": "HR wants to review sends"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    prompts.current_tenant.set(TENANT)
    assert tiering.get_tier("talent-workforce-intelligence", "email.send", {}) == 2
    prompts.current_tenant.set("another-tenant")
    assert tiering.get_tier("talent-workforce-intelligence", "email.send", {}) == 1

    listed = (await client.get(URL, headers=TENANT_HEADERS)).json()["actions"]
    row = next(a for a in listed if a["action_type"] == "email.send")
    assert (row["base_tier"], row["tier"], row["override"]["reason"]) == (1, 2, "HR wants to review sends")

    assert (await client.delete(f"{URL}/talent-workforce-intelligence/email.send", headers=TENANT_HEADERS)).status_code == 204
    prompts.current_tenant.set(TENANT)
    assert tiering.get_tier("talent-workforce-intelligence", "email.send", {}) == 1


async def test_removing_an_approval_gate_needs_evidence(client, db_session):
    """deal.structure_recommend is tier 2 in the policy file."""
    agent, action = "sales-originations-intelligence", "deal.structure_recommend"
    refused = await client.put(f"{URL}/{agent}/{action}", json={"tier": 1, "reason": "it's always approved"}, headers=TENANT_HEADERS)
    assert refused.status_code == 422
    assert "evidence" in refused.json()["detail"]

    await _decisions(db_session, action, approved=19, rejected=3, policy=agent)
    weak = (await client.get(f"{URL}/{agent}/{action}/evidence", headers=TENANT_HEADERS)).json()
    assert weak["decided"] == 22 and weak["sufficient"] is False  # 86% approval

    await _decisions(db_session, action, approved=10, rejected=0, policy=agent)
    strong = (await client.get(f"{URL}/{agent}/{action}/evidence", headers=TENANT_HEADERS)).json()
    assert strong["sufficient"] is True
    promoted = await client.put(f"{URL}/{agent}/{action}", json={"tier": 1, "reason": "90-day review"}, headers=TENANT_HEADERS)
    assert promoted.status_code == 200
    assert "approved" in promoted.json()["evidence"]


async def test_a_stored_override_on_a_tier_3_action_is_ignored(db_session):
    """Defense in depth: even a row written around the API can't lower tier 3."""
    tiering._overrides[(TENANT, "sales-originations-intelligence", "credit.decision")] = (1, None)
    prompts.current_tenant.set(TENANT)
    assert tiering.get_tier("sales-originations-intelligence", "credit.decision", {}) == 3


async def test_refresh_loads_changes_made_on_other_replicas(client, db_session):
    await client.put(f"{URL}/talent-workforce-intelligence/email.send", json={"tier": 2, "reason": "review"}, headers=TENANT_HEADERS)
    tiering._overrides.clear()
    assert await tier_overrides.refresh(db_session) == 1
    prompts.current_tenant.set(TENANT)
    assert tiering.get_tier("talent-workforce-intelligence", "email.send", {}) == 2
