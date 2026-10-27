"""Super admin control of every agent: the registry lists all agents the
supervisor can dispatch to, and the kill switch works per agent and per
tenant without a registration step."""
from sqlalchemy import select

from src.models import DecisionLog
from src.services import supervisor
from tests.conftest import TENANT_HEADERS

URL = "/api/v1/ai/agents/registry"
OTHER = {"X-Tenant-Id": "another-tenant"}


async def test_registry_lists_every_dispatchable_agent(client):
    agents = (await client.get(URL, headers=TENANT_HEADERS)).json()
    keys = {a["key"] for a in agents}
    dispatchable = {key for routes in supervisor.EVENT_ROUTING.values() for key, _ in routes}
    assert dispatchable <= keys
    revenue = next(a for a in agents if a["key"] == "revenue_agent")
    assert revenue["status"] == "active" and revenue["registered"] is False
    assert "lead.captured" in revenue["triggers"]
    assert "Relationship Intelligence" in revenue["roster"]


async def test_pausing_an_agent_stops_it_handling_events(client, db_session):
    resp = await client.put(f"{URL}/revenue_agent/status", json={"status": "paused"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200 and resp.json()["status"] == "paused"
    results = await supervisor.route_event(db_session, TENANT_HEADERS["X-Tenant-Id"], "lead.captured", {"lead_id": "x", "name": "A"})
    assert [type(r).__name__ for r in results] == ["SkippedEvaluation"]
    row = next(a for a in (await client.get(URL, headers=TENANT_HEADERS)).json() if a["key"] == "revenue_agent")
    assert (row["status"], row["registered"]) == ("paused", True)


async def test_tenants_control_the_same_agent_independently(client):
    assert (await client.put(f"{URL}/concierge_agent/status", json={"status": "disabled"}, headers=TENANT_HEADERS)).status_code == 200
    assert (await client.put(f"{URL}/concierge_agent/status", json={"status": "paused"}, headers=OTHER)).status_code == 200
    mine = next(a for a in (await client.get(URL, headers=TENANT_HEADERS)).json() if a["key"] == "concierge_agent")
    theirs = next(a for a in (await client.get(URL, headers=OTHER)).json() if a["key"] == "concierge_agent")
    assert (mine["status"], theirs["status"]) == ("disabled", "paused")


async def test_kill_switch_changes_are_audited(client, db_session):
    await client.put(f"{URL}/pricing_intelligence_agent/status", json={"status": "paused"}, headers=TENANT_HEADERS)
    await client.put(f"{URL}/pricing_intelligence_agent/status", json={"status": "active"}, headers=TENANT_HEADERS)
    audits = (await db_session.execute(
        select(DecisionLog).where(DecisionLog.trigger_event_type == "feature_flag.kill_switch_toggled")
    )).scalars().all()
    assert len(audits) == 2


async def test_unknown_agents_are_refused(client):
    resp = await client.put(f"{URL}/made_up_agent/status", json={"status": "paused"}, headers=TENANT_HEADERS)
    assert resp.status_code == 404
