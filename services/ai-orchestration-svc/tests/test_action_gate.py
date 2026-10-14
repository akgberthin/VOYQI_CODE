"""The three-way autonomy branch (tiering instructions §1.2), end to end:
tier 1 executes now; tier 2 queues and executes only on approval; tier
3 additionally refuses approval without an accountable role."""
import pytest
from sqlalchemy import select

from src.core import action_gate
from src.models import AIRecommendation, RecommendationStatus
from src.services import hrm_tools, talent_agent
from tests.conftest import TENANT_HEADERS

TENANT = TENANT_HEADERS["X-Tenant-Id"]


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(hrm_tools, "send_email", lambda tenant_id, email_id: calls.append(email_id) or {"status": "sent"})
    return calls


def _force_tier(monkeypatch, tier):
    monkeypatch.setattr(action_gate, "get_tier", lambda agent, action_type, context=None: tier)


async def _gate(db_session):
    return await action_gate.gate(
        db_session, tenant_id=TENANT, agent_key="communication_agent", policy_agent="talent-workforce-intelligence",
        action_type="email.send", executor="hrm.send_email", args={"email_id": "em-1"}, reasoning="r",
        triggering_event_type="hrm.email_draft_requested", domain="hrm",
    )


async def test_tier_1_executes_immediately(db_session, monkeypatch, sent):
    _force_tier(monkeypatch, 1)
    result = await _gate(db_session)
    assert (result.tier, result.executed, result.recommendation) == (1, True, None)
    assert sent == ["em-1"]


async def test_tier_2_queues_and_executes_only_when_approved(client, db_session, monkeypatch, sent):
    _force_tier(monkeypatch, 2)
    result = await _gate(db_session)
    await db_session.commit()
    assert (result.tier, result.executed) == (2, False)
    assert sent == []

    resp = await client.post(f"/api/v1/ai/recommendations/{result.recommendation.id}/approve", json={"reason_code": "ok"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert sent == ["em-1"]


async def test_rejected_tier_2_action_never_executes(client, db_session, monkeypatch, sent):
    _force_tier(monkeypatch, 2)
    result = await _gate(db_session)
    await db_session.commit()
    await client.post(f"/api/v1/ai/recommendations/{result.recommendation.id}/reject", json={"reason_code": "no"}, headers=TENANT_HEADERS)
    assert sent == []


async def test_tier_3_cannot_be_approved_without_accountable_role(client, db_session, monkeypatch, sent):
    _force_tier(monkeypatch, 3)
    result = await _gate(db_session)
    await db_session.commit()
    assert result.recommendation.requires_accountable_role is True

    blocked = await client.post(f"/api/v1/ai/recommendations/{result.recommendation.id}/approve", json={"reason_code": "ok"}, headers=TENANT_HEADERS)
    assert blocked.status_code == 400
    assert sent == []

    approved = await client.post(
        f"/api/v1/ai/recommendations/{result.recommendation.id}/approve",
        json={"reason_code": "ok", "accountable_role": "hr_director"}, headers=TENANT_HEADERS,
    )
    assert approved.status_code == 200
    assert sent == ["em-1"]


async def test_unknown_executor_is_rejected_before_anything_runs(db_session):
    with pytest.raises(KeyError):
        await action_gate.gate(
            db_session, tenant_id=TENANT, agent_key="a", policy_agent="p", action_type="x", executor="nope",
            args={}, reasoning="r", triggering_event_type="e", domain="d",
        )


async def test_flipping_an_hrm_action_to_tier_2_stops_the_agent_sending(db_session, monkeypatch):
    """HRM ships at tier 1 to match its full-automation build; moving
    any action to tier 2 in tier-policy.yaml must be enough on its own
    to stop the agent executing it."""
    _force_tier(monkeypatch, 2)
    sent_drafts = []
    monkeypatch.setattr(hrm_tools, "create_nurture_draft", lambda *a, **k: {"id": "draft-9"})
    monkeypatch.setattr(hrm_tools, "send_nurture_draft", lambda tenant_id, draft_id: sent_drafts.append(draft_id) or {})

    class _Unconfigured:
        is_configured = False

    result = await talent_agent.evaluate_candidate_nurture(db_session, TENANT, "cand-1", "app-1", "Alex", "role filled", client=_Unconfigured())

    assert result.draft_created is True
    assert result.draft_sent is False
    assert sent_drafts == []
    rec = (await db_session.execute(select(AIRecommendation))).scalar_one()
    assert rec.status == RecommendationStatus.PENDING
    assert rec.proposed_action == {"executor": "hrm.send_nurture_draft", "args": {"draft_id": "draft-9"}, "policy": "talent-workforce-intelligence.candidate.nurture_send"}


def test_every_gated_executor_is_registered():
    """Each executor key a call site uses must exist, or the first tier-2
    approval of that action would fail at execution time."""
    import re
    from pathlib import Path

    used = set()
    for path in Path("src/services").glob("*_agent.py"):
        source = path.read_text()
        used |= set(re.findall(r'executor="([a-z_.]+)"', source))
        used |= set(re.findall(r'_hrm_gate\(\s*db, tenant_id, \w+, "[a-z_.]+", "([a-z_.]+)"', source))
        used |= set(re.findall(r'executor, args, tool, verb = "([a-z_.]+)"', source))
    assert len(used) >= 15  # the regexes are actually finding the call sites
    assert used <= set(action_gate.EXECUTORS), used - set(action_gate.EXECUTORS)


async def test_tier_graduation_report_counts_approvals_and_overrides(client, db_session, monkeypatch, sent):
    _force_tier(monkeypatch, 2)
    approved = (await _gate(db_session)).recommendation
    rejected = (await _gate(db_session)).recommendation
    await db_session.commit()
    await client.post(f"/api/v1/ai/recommendations/{approved.id}/approve", json={"reason_code": "ok"}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/ai/recommendations/{rejected.id}/reject", json={"reason_code": "no"}, headers=TENANT_HEADERS)

    report = (await client.get("/api/v1/ai/audit/tier-graduation", headers=TENANT_HEADERS)).json()
    [entry] = report["gated_actions"]
    assert (entry["agent_key"], entry["action_type"], entry["tier"]) == ("communication_agent", "email.send", 2)
    assert (entry["decided"], entry["approval_rate"], entry["override_rate"], entry["promotable"]) == (2, 0.5, 0.5, True)
