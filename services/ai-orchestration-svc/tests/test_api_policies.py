"""AI-003 Prompt & Policy Manager: versioned prompts, a server-side
evaluation gate, and promoted versions actually reaching the agents."""
from types import SimpleNamespace

import pytest

from src.core import prompts
from src.core.anthropic_client import AnthropicAgentClient
from src.services import concierge_agent
from tests.conftest import TENANT_HEADERS

KEY = "concierge_agent.MESSAGE_ASSESSMENT_SYSTEM_PROMPT"
DEFAULT = concierge_agent.MESSAGE_ASSESSMENT_SYSTEM_PROMPT
GOOD = DEFAULT + "\nKeep replies under 120 words."
BROKEN = "You are a helpful concierge. Be warm."  # drops the tools the agent relies on


@pytest.fixture(autouse=True)
def _clean_cache():
    prompts._active.clear()
    yield
    prompts._active.clear()


async def _draft(client, text, key=KEY):
    resp = await client.post("/api/v1/ai/policies", json={"prompt_key": key, "system_prompt": text}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    return resp.json()


async def test_registry_lists_every_agent_prompt_with_what_it_must_keep(client):
    listed = (await client.get("/api/v1/ai/prompts", headers=TENANT_HEADERS)).json()
    entry = next(p for p in listed if p["prompt_key"] == KEY)
    assert entry["agent_key"] == "concierge_agent"
    assert "submit_message_assessment" in entry["required"]
    assert "Always finish by calling" in entry["required"]
    assert entry["active_version"] is None
    assert len(listed) == len(prompts.registry())


async def test_unknown_prompt_key_rejected(client):
    resp = await client.post("/api/v1/ai/policies", json={"prompt_key": "nope.PROMPT", "system_prompt": "x"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


async def test_versions_increment_per_prompt(client):
    assert (await _draft(client, GOOD))["version"] == 1
    assert (await _draft(client, GOOD + " v2"))["version"] == 2
    other = await _draft(client, prompts.registry()["revenue_agent.FUNDING_MATCH_SYSTEM_PROMPT"], key="revenue_agent.FUNDING_MATCH_SYSTEM_PROMPT")
    assert other["version"] == 1


async def test_cannot_promote_without_server_side_evaluation(client):
    draft = await _draft(client, GOOD)
    resp = await client.post(f"/api/v1/ai/policies/{draft['id']}/promote", json={"eval_suite_passed": True}, headers=TENANT_HEADERS)
    assert resp.status_code == 422  # asserting it passed is not enough


async def test_evaluation_catches_a_prompt_that_would_break_the_agent(client):
    draft = await _draft(client, BROKEN)
    evaluated = (await client.post(f"/api/v1/ai/policies/{draft['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert evaluated["eval_suite_passed"] is False
    assert "submit_message_assessment" in evaluated["eval_summary"]
    assert (await client.post(f"/api/v1/ai/policies/{draft['id']}/promote", headers=TENANT_HEADERS)).status_code == 422


async def test_promoted_version_is_what_claude_receives_and_rollback_works(client):
    v1 = await _draft(client, GOOD)
    await client.post(f"/api/v1/ai/policies/{v1['id']}/evaluate", headers=TENANT_HEADERS)
    promoted = await client.post(f"/api/v1/ai/policies/{v1['id']}/promote", headers={**TENANT_HEADERS, "X-Actor-Id": "ai-lead"})
    assert promoted.json()["status"] == "active"

    sent = []
    agent_client = AnthropicAgentClient()
    agent_client._client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: sent.append(kw["system"]) or SimpleNamespace(content=[], stop_reason="end_turn")
    ))
    prompts.current_tenant.set(TENANT_HEADERS["X-Tenant-Id"])
    agent_client.run_agent_turn(system_prompt=DEFAULT, user_message="hi", tools=[], tool_dispatch={})
    assert sent[-1] == GOOD

    # another tenant still gets the code's default
    prompts.current_tenant.set("another-tenant")
    agent_client.run_agent_turn(system_prompt=DEFAULT, user_message="hi", tools=[], tool_dispatch={})
    assert sent[-1] == DEFAULT

    # promoting v2 retires v1
    v2 = await _draft(client, GOOD + " Sign off as the VOYQI team.")
    await client.post(f"/api/v1/ai/policies/{v2['id']}/evaluate", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/ai/policies/{v2['id']}/promote", headers=TENANT_HEADERS)
    statuses = {p["version"]: p["status"] for p in (await client.get("/api/v1/ai/policies", params={"agent_key": "concierge_agent"}, headers=TENANT_HEADERS)).json()}
    assert statuses == {1: "retired", 2: "active"}
    listed = (await client.get("/api/v1/ai/prompts", headers=TENANT_HEADERS)).json()
    assert next(p for p in listed if p["prompt_key"] == KEY)["active_version"] == 2


async def test_refresh_loads_promotions_made_by_other_replicas(client, db_session):
    draft = await _draft(client, GOOD)
    await client.post(f"/api/v1/ai/policies/{draft['id']}/evaluate", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/ai/policies/{draft['id']}/promote", headers=TENANT_HEADERS)
    prompts._active.clear()  # as on a replica that didn't handle the promotion

    assert await prompts.refresh(db_session) == 1
    prompts.current_tenant.set(TENANT_HEADERS["X-Tenant-Id"])
    assert prompts.resolve(DEFAULT) == GOOD


def test_every_default_prompt_passes_its_own_evaluation():
    for key, text in prompts.registry().items():
        assert prompts.evaluate(key, text) == (True, []), key
