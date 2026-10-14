"""Tiering sign-off checklist (instructions §6): every agent action has an
explicit tier-policy entry — nothing relies on default_tier silently —
and every decision is logged with its tier."""
import re
from pathlib import Path

import yaml

from src.core.tiering import DECISION_ACTIONS, tier_for_decision
from src.models import DecisionLog

POLICY = yaml.safe_load(Path("src/core/tier_policy.yaml").read_text())


def _explicit(policy_agent: str, action: str) -> bool:
    return action in POLICY["agents"].get(policy_agent, {}).get("actions", {})


def test_every_mapped_decision_has_an_explicit_policy_entry():
    missing = [f"{agent}.{action}" for agent, action in DECISION_ACTIONS.values() if not _explicit(agent, action)]
    assert missing == []


def test_every_gated_action_has_an_explicit_policy_entry():
    used = set()
    for path in Path("src/services").glob("*_agent.py"):
        source = path.read_text()
        used |= {("talent-workforce-intelligence", a) for a in re.findall(r'_hrm_gate\(\s*db, tenant_id, \w+, "([a-z_.]+)"', source)}
        used |= {("dealer-operations", a) for a in re.findall(r'policy_action="([a-z_.]+)"', source)}
    used.discard(("dealer-operations", "deal.structure_recommend"))  # routed to sales-originations-intelligence
    missing = [f"{agent}.{action}" for agent, action in used if not _explicit(agent, action)]
    assert len(used) >= 15
    assert missing == []


def test_every_agent_decision_type_in_code_is_mapped():
    """Each literal (agent_key, trigger_event_type) a DecisionLog is
    written with resolves to a tier — either directly or via the
    agent's "*" entry."""
    unmapped = []
    for path in Path("src/services").glob("*_agent.py"):
        source = path.read_text()
        keys = dict(re.findall(r'^(AGENT_KEY\w*)\s*=\s*"([a-z_]+)"', source, re.M))
        for agent_var, trigger in re.findall(r'agent_key=(AGENT_KEY\w*), trigger_event_type="([a-z_.]+)"', source):
            if tier_for_decision(keys[agent_var], trigger) is None:
                unmapped.append((keys[agent_var], trigger))
    assert unmapped == []


def test_tier_3_actions_stay_tier_3():
    tier_3 = {f"{a}.{x}" for a, cfg in POLICY["agents"].items() for x, v in cfg["actions"].items() if v["tier"] == 3}
    assert tier_3 == {
        "fleet-intelligence.incident.liability_assessment",
        "fleet-intelligence.telematics.tamper_event_flag",
        "sales-originations-intelligence.credit.decision",
        "talent-workforce-intelligence.hire_reject_discipline.decision",
        "compliance-risk-intelligence.abac.policy_grant_apply",
    }


async def test_decision_log_is_tagged_on_insert(db_session):
    log = DecisionLog(
        tenant_id="t", agent_key="revenue_agent", trigger_event_type="org.decisioning_requested", trigger_context={},
        reasoning="r", tools_called=[], action_taken="risk_score_applied", outcome="success",
    )
    db_session.add(log)
    await db_session.commit()
    assert (log.tier, log.approval_required) == (1, False)


async def test_explicit_tier_and_human_overrides_are_not_retagged(db_session):
    explicit = DecisionLog(
        tenant_id="t", agent_key="revenue_agent", trigger_event_type="org.decisioning_requested", trigger_context={},
        reasoning="r", tools_called=[], action_taken="x", outcome="deferred", tier=3, approval_required=True,
    )
    override = DecisionLog(
        tenant_id="t", agent_key="revenue_agent", trigger_event_type="org.decisioning_requested", trigger_context={},
        reasoning="r", tools_called=[], action_taken="human_approved_and_executed", outcome="success", is_override=True,
    )
    db_session.add_all([explicit, override])
    await db_session.commit()
    assert explicit.tier == 3
    assert override.tier is None
