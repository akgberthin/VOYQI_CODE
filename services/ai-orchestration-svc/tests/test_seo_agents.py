"""The Growth / SEO agents against a stand-in SEO engine: claiming,
working as the owning agent, falling back without Claude, blocking on a
missing integration, the kill switch, cycles and intake."""
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from src.models import Agent, AgentStatus, DecisionLog
from src.services import seo_agent, seo_tools
from tests.conftest import TENANT_HEADERS

TENANT = "voyqi-holdings"
SITE = {"id": "s1", "name": "VOYQI", "domain": "voyqi.com", "report_frequency": "weekly", "report_recipients": [],
        "business_profile": {"description": "Portable mobility on a monthly plan", "products": ["car subscription", "ev subscription"]}}
BRIEF = {"keyword": "ev subscription cost", "kind": "blog", "intent": "commercial", "purchase_intent": 75, "format": "listicle",
         "angle": "a ranked, honest comparison list", "must_include": ["pricing", "who each option suits"], "secondary_keywords": [],
         "questions_to_answer": ["is an ev subscription worth it"], "internal_link_targets": [{"title": "Plans", "url": "https://voyqi.com/plans"}],
         "outline": [], "target_words": 800}


class FakeEngine:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.tasks = {
            "t1": {"id": "t1", "site_id": "s1", "type": "content_create", "title": "Create content for 'ev subscription cost'",
                   "params": {"keyword": "ev subscription cost"}, "owning_agent": "seo_content_agent", "status": "ready"},
            "t2": {"id": "t2", "site_id": "s1", "type": "keyword_research", "title": "Keyword research", "params": {},
                   "owning_agent": "keyword_research_agent", "status": "ready"},
            "t3": {"id": "t3", "site_id": "s1", "type": "content_refresh", "title": "Refresh /ev", "params": {"url": "https://voyqi.com/ev"},
                   "owning_agent": "seo_content_agent", "status": "ready"},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1/marketing/seo")
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, dict(request.headers), body))

        def ok(data, status=200):
            return httpx.Response(status, json=data)

        if m := re.fullmatch(r"/tasks/(\w+)", path):
            return ok(self.tasks[m.group(1)])
        if m := re.fullmatch(r"/tasks/(\w+)/claim", path):
            task = self.tasks[m.group(1)]
            if task["status"] != "ready":
                return ok({"detail": "not ready", "code": "conflict"}, 409)
            task["status"] = "in_progress"
            return ok(task)
        if m := re.fullmatch(r"/tasks/(\w+)/(complete|fail)", path):
            task = self.tasks[m.group(1)]
            task["status"] = "done" if m.group(2) == "complete" else ("blocked" if body.get("blocked") else "failed")
            task["finish"] = body
            return ok(task)
        if path == "/sites/s1":
            return ok(SITE)
        if path == "/sites/s1/content/brief":
            return ok(BRIEF)
        if path == "/sites/s1/content":
            return ok({"id": "c1", "status": "planned"}, 201)
        if path == "/content/c1/draft":
            held = body.get("written_by") == "template"
            return ok({"id": "c1", "title": body["title"], "seo_score": 88, "quality_findings": [],
                       "status": "held_for_review" if held else "approved"})
        if path == "/content/c1/schedule":
            return ok({"id": "c1", "title": "x", "seo_score": 88, "status": "scheduled"})
        if path == "/sites/s1/keywords/sync":
            return ok({"detail": "keyword research needs the keyword-data or Search Console integration connected",
                       "code": "provider_not_configured"}, 409)
        if path == "/sites/s1/pages/read":
            return ok({"url": body["url"], "title": "EV", "meta_description": "", "h1": ["EV subscription"], "h2": [], "words": 300,
                       "text": "Old text.", "images": []})
        if path == "/sites/s1/replan":
            return ok({"queued": ["report"]})
        if path == "/sites/s1/autopilot/plan":
            return ok({"mode": "autopilot", "in_progress": 0, "slots_free": 5, "caps": {},
                       "eligible": [{"task_id": "t1"}, {"task_id": "t2"}], "held": [{"task_id": "t9", "type": "link_outreach", "reason": "cap reached"}]})
        if path == "/requests/r1/convert":
            return ok({"id": "r1", "sender": "ops@voyqi.com", "subject": "Audit", "status": "converted", "task_ids": ["t7"],
                       "injection_suspected": False})
        return ok({"detail": f"no route {request.method} {path}"}, 404)


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    transport = httpx.MockTransport(fake.handle)

    def client(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    monkeypatch.setattr(seo_tools, "httpx", SimpleNamespace(AsyncClient=client, HTTPError=httpx.HTTPError))
    return fake


class NoClaude:
    is_configured = False


class FakeClaude:
    is_configured = True

    def __init__(self, answer: dict):
        self.answer, self.prompts = answer, []

    def complete(self, system_prompt, user, max_tokens=None):
        self.prompts.append(system_prompt)
        return json.dumps(self.answer)


async def test_content_without_claude_is_written_from_template_and_held(db_session, engine):
    result = await seo_agent.run_task(db_session, TENANT, "t1", NoClaude())
    assert result["outcome"] == "success" and "held for review" in result["detail"]
    draft = next(body for method, path, _h, body in engine.calls if path == "/content/c1/draft")
    assert draft["written_by"] == "template" and draft["body_markdown"].startswith("# ")
    assert not any(path == "/content/c1/schedule" for _m, path, _h, _b in engine.calls)  # held, so not scheduled
    # Every call was made as the owning agent.
    headers = [h for _m, path, h, _b in engine.calls if path != "/tasks/t1"]
    assert all(h["x-ai-initiated"] == "seo_content_agent" for h in headers)
    log = (await db_session.execute(select(DecisionLog).where(DecisionLog.agent_key == "seo_content_agent"))).scalar_one()
    assert "deterministic fallback" in log.reasoning and log.outcome == "success"


async def test_content_with_claude_is_scheduled(db_session, engine):
    claude = FakeClaude({"title": "EV subscription cost: what members pay", "meta_description": "m" * 120, "body_markdown": "# EV\n\ntext"})
    result = await seo_agent.run_task(db_session, TENANT, "t1", claude)
    assert result["outcome"] == "success" and "scheduled" in result["detail"]
    assert claude.prompts == [seo_agent.SEO_WRITER_SYSTEM_PROMPT]
    assert engine.tasks["t1"]["status"] == "done"


async def test_missing_integration_blocks_the_task(db_session, engine):
    result = await seo_agent.run_task(db_session, TENANT, "t2", NoClaude())
    assert result["outcome"] == "deferred" and engine.tasks["t2"]["status"] == "blocked"


async def test_live_pages_are_never_replaced_by_the_template_writer(db_session, engine):
    result = await seo_agent.run_task(db_session, TENANT, "t3", NoClaude())
    assert result["outcome"] == "deferred" and "AI writer" in result["detail"]
    assert not any(path == "/content/c1/draft" for _m, path, _h, _b in engine.calls)


async def test_paused_agent_does_not_claim(db_session, engine):
    db_session.add(Agent(tenant_id=TENANT, key="seo_content_agent", display_name="SEO Content Agent", status=AgentStatus.PAUSED))
    await db_session.commit()
    result = await seo_agent.run_task(db_session, TENANT, "t1", NoClaude())
    assert result["outcome"] == "skipped" and engine.tasks["t1"]["status"] == "ready"


async def test_cycle_runs_what_the_plan_allows(db_session, engine):
    result = await seo_agent.run_cycle(db_session, TENANT, "s1", NoClaude())
    assert [r["task_id"] for r in result["ran"]] == ["t1", "t2"]
    assert "1 held" in result["summary"]
    log = (await db_session.execute(select(DecisionLog).where(DecisionLog.agent_key == "seo_autopilot_agent"))).scalar_one()
    assert "cap reached" in log.reasoning


async def test_paused_autopilot_runs_nothing(db_session, engine):
    db_session.add(Agent(tenant_id=TENANT, key="seo_autopilot_agent", display_name="SEO Autopilot Agent", status=AgentStatus.DISABLED))
    await db_session.commit()
    result = await seo_agent.run_cycle(db_session, TENANT, "s1", NoClaude())
    assert result["ran"] == [] and not engine.calls


async def test_intake_converts_as_the_intake_agent(db_session, engine):
    result = await seo_agent.intake(db_session, TENANT, "r1")
    assert result["status"] == "converted"
    assert engine.calls[0][2]["x-ai-initiated"] == "seo_task_intake_agent"


async def test_registry_lists_the_fourteen_agents(client, db_session):
    agents = (await client.get("/api/v1/ai/seo/agents", headers=TENANT_HEADERS)).json()
    assert len(agents) == 14 and {a["key"] for a in agents} == set(seo_agent.AGENT_KEYS)
    registry = {a["key"]: a for a in (await client.get("/api/v1/ai/agents/registry", headers=TENANT_HEADERS)).json()}
    assert "seo.autopilot_tick" in registry["seo_autopilot_agent"]["triggers"]
    r = await client.put("/api/v1/ai/agents/registry/geo_agent/status", json={"status": "paused"}, headers=TENANT_HEADERS)
    assert r.status_code == 200
    agents = {a["key"]: a for a in (await client.get("/api/v1/ai/seo/agents", headers=TENANT_HEADERS)).json()}
    assert agents["geo_agent"]["status"] == "paused"


def test_templates_stay_inside_the_guardrail_words():
    banned = ["guaranteed", "risk-free", "no risk", "best in the world", "unbeatable", "#1 rated", "number one"]
    article = seo_agent.template_article(BRIEF, SITE)
    text = json.dumps(article).lower()
    assert not any(b in text for b in banned)
    outreach = seo_agent.template_outreach({"domain": "x.example"}, SITE, "https://voyqi.com/")
    assert "unsubscribe" in outreach["body"] and "pay" not in outreach["body"].lower()
    loc = {"name": "VOYQI Phoenix Hub", "address": {"city": "Tempe"}, "services": ["car subscription", "ev subscription"], "website": "voyqi.com/hubs"}
    description = seo_agent.template_description(loc, SITE)
    assert description is None or len(description) >= 250
