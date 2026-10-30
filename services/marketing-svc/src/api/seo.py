"""Growth / SEO API — /api/v1/marketing/seo/...

The same routes serve the marketing console and the SEO agents; a
request carrying X-AI-Initiated acts as that agent and meets every hard
gate a person would not."""
import hmac
import uuid

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import integrations as tenant_integrations
from src.core.database import get_db
from src.core.security import get_tenant_id
from src.seo import (
    adapters,
    agents_bridge,
    content,
    cro,
    geo,
    intake,
    keywords,
    links,
    local,
    measure,
    pseo,
    reports,
    research,
    sites,
    strategy,
    tasks,
    technical,
)
from src.seo.common import SeoError, actor_of, get_owned, get_site, row
from src.seo.constants import GEO_ENGINES, INTEGRATION_KINDS
from src.seo.models import SeoCompetitor, SeoReport, SeoSite
from src.seo.rules import RuleError, build_schema, resolve_redirects, validate_schema

router = APIRouter(prefix="/api/v1/marketing/seo", tags=["seo"])
public_router = APIRouter(prefix="/api/v1/marketing/public/seo", tags=["seo-public"])


async def seo_error_handler(_request: Request, exc: SeoError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail, "code": exc.code, **exc.extra})


async def rule_error_handler(_request: Request, exc: RuleError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "code": "invalid", **exc.extra})


def actor(x_user_id: str | None = Header(default=None), x_ai_initiated: str | None = Header(default=None)) -> str:
    return actor_of(x_user_id, x_ai_initiated)


Tenant = Depends(get_tenant_id)
Db = Depends(get_db)
Actor = Depends(actor)
JsonBody = Body(default_factory=dict)


# ── reference ─────────────────────────────────────────────────────

@router.get("/task-types")
async def task_types():
    return tasks.task_types()


@router.get("/integration-kinds")
async def integration_kinds():
    return [{"kind": k, "label": label, "powers": powers, "providers": list(providers)} for k, (label, powers, providers) in INTEGRATION_KINDS.items()]


@router.get("/technical/fix-policy")
async def fix_policy():
    return technical.fix_policy()


@router.get("/links/gate")
async def link_gate():
    return links.gate()


@router.get("/local/checklist")
async def local_checklist():
    return local.checklist()


@router.get("/agents")
async def agent_roster(tenant_id: str = Tenant):
    return await agents_bridge.agents(tenant_id)


# ── sites ─────────────────────────────────────────────────────────

@router.get("/sites")
async def list_sites(tenant_id: str = Tenant, db: AsyncSession = Db):
    return [row(s) for s in await sites.list_sites(db, tenant_id)]


@router.post("/sites", status_code=201)
async def create_site(body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await sites.create_site(db, tenant_id, body, who))


@router.get("/sites/{site_id}")
async def get_site_route(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return row(await get_site(db, tenant_id, site_id))


@router.patch("/sites/{site_id}")
async def update_site(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await sites.update_site(db, tenant_id, site_id, body, who))


@router.get("/sites/{site_id}/activity")
async def site_activity(site_id: str, kind: str | None = None, limit: int = 50, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await sites.list_activity(db, tenant_id, site_id, kind, limit)


@router.get("/sites/{site_id}/dashboard")
async def site_dashboard(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await reports.dashboard(db, tenant_id, site_id)


@router.post("/sites/{site_id}/cycle")
async def run_cycle(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    """Runs the autopilot cycle now instead of waiting for the next one."""
    await get_site(db, tenant_id, site_id)
    return await agents_bridge.run_cycle(tenant_id, site_id)


@router.post("/sites/{site_id}/replan")
async def replan(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    site = await get_site(db, tenant_id, site_id)
    return {"queued": await tasks.replan(db, site, who)}


# ── integrations ──────────────────────────────────────────────────

@router.get("/sites/{site_id}/integrations")
async def list_integrations(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await sites.list_integrations(db, tenant_id, site_id)


@router.put("/sites/{site_id}/integrations/{kind}")
async def configure_integration(site_id: str, kind: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await sites.configure_integration(db, tenant_id, site_id, kind, body, who)


@router.post("/sites/{site_id}/integrations/{kind}/test")
async def test_integration(site_id: str, kind: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await sites.test_integration(db, tenant_id, site_id, kind, who)


@router.delete("/sites/{site_id}/integrations/{kind}")
async def disconnect_integration(site_id: str, kind: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await sites.disconnect_integration(db, tenant_id, site_id, kind, who)


@router.post("/sites/{site_id}/integrations/sync")
async def sync_integrations(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await measure.sync_all(db, await get_site(db, tenant_id, site_id), who)


# ── tasks and the autopilot plan ──────────────────────────────────

@router.get("/sites/{site_id}/tasks")
async def list_tasks(site_id: str, status: str | None = None, type: str | None = None, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await tasks.list_tasks(db, tenant_id, site_id, status, type)


@router.post("/sites/{site_id}/tasks", status_code=201)
async def create_task(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    site = await get_site(db, tenant_id, site_id)
    body = {k: v for k, v in body.items() if k != "injection"}  # only intake decides that
    task, created = await tasks.create_task(db, site, body, who)
    return {**row(task), "created": created}


@router.get("/sites/{site_id}/autopilot/plan")
async def autopilot_plan(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await tasks.plan(db, await get_site(db, tenant_id, site_id))


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await tasks.task_for_agent(db, tenant_id, task_id)


@router.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await tasks.approve(db, tenant_id, task_id, who))


@router.post("/tasks/{task_id}/dismiss")
async def dismiss_task(task_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await tasks.dismiss(db, tenant_id, task_id, who))


@router.post("/tasks/{task_id}/claim")
async def claim_task(task_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await tasks.claim(db, tenant_id, task_id, who))


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await tasks.finish(db, tenant_id, task_id, "done", body, who))


@router.post("/tasks/{task_id}/fail")
async def fail_task(task_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await tasks.finish(db, tenant_id, task_id, "blocked" if body.get("blocked") else "failed", body, who))


# ── strategy ──────────────────────────────────────────────────────

@router.get("/sites/{site_id}/strategy")
async def get_strategy(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await strategy.current(db, tenant_id, site_id)


@router.post("/sites/{site_id}/strategy/generate", status_code=201)
async def generate_strategy(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await strategy.generate(db, tenant_id, site_id, who)


@router.get("/sites/{site_id}/strategy/history")
async def strategy_history(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await strategy.history(db, tenant_id, site_id)


@router.post("/strategy/{strategy_id}/narrative")
async def strategy_narrative(strategy_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await strategy.set_narrative(db, tenant_id, strategy_id, body.get("narrative") or "")


# ── keywords, competitors, research ───────────────────────────────

@router.get("/sites/{site_id}/keywords")
async def list_keywords(site_id: str, request: Request, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await keywords.list_keywords(db, tenant_id, site_id, dict(request.query_params))


@router.get("/sites/{site_id}/keywords/gaps")
async def keyword_gaps(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await keywords.gaps(db, tenant_id, site_id)


@router.get("/sites/{site_id}/keywords/questions")
async def keyword_questions(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await keywords.questions(db, tenant_id, site_id)


@router.post("/sites/{site_id}/keywords/sync")
async def sync_keywords(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await keywords.sync(db, await get_site(db, tenant_id, site_id), who)


@router.post("/sites/{site_id}/keywords/import")
async def import_keywords(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await keywords.import_keywords(db, tenant_id, site_id, body, who)


@router.get("/sites/{site_id}/competitors")
async def list_competitors(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    site = await get_site(db, tenant_id, site_id)
    return [row(c) for c in (await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id))).scalars()]


@router.post("/sites/{site_id}/competitors", status_code=201)
async def add_competitor(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await sites.add_competitor(db, tenant_id, site_id, body.get("domain") or "", str(body.get("notes") or ""), who))


@router.get("/sites/{site_id}/research/items")
async def research_items(site_id: str, include_flagged: bool = False, source: str | None = None, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await research.list_items(db, tenant_id, site_id, include_flagged, source)


@router.get("/sites/{site_id}/research/sources")
async def research_sources(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await research.sources(db, tenant_id, site_id)


@router.get("/sites/{site_id}/research/digest")
async def research_digest(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await research.digest(db, tenant_id, site_id)


@router.post("/sites/{site_id}/research/run")
async def run_research(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await research.run(db, await get_site(db, tenant_id, site_id), who, body.get("sources"))


@router.post("/sites/{site_id}/research/search-intent")
async def search_intent(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await research.search_intent(db, tenant_id, site_id, body.get("query") or "")


# ── content ───────────────────────────────────────────────────────

@router.post("/sites/{site_id}/content/brief")
async def content_brief(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await content.brief(db, tenant_id, site_id, body.get("keyword") or "", body.get("kind"))


@router.get("/sites/{site_id}/content")
async def list_content(site_id: str, status: str | None = None, kind: str | None = None, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await content.list_content(db, tenant_id, site_id, status, kind)


@router.post("/sites/{site_id}/content", status_code=201)
async def create_content(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return content.content_out(await content.create(db, tenant_id, site_id, body, who))


@router.get("/sites/{site_id}/content/refresh-candidates")
async def refresh_candidates(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await content.refresh_candidates(db, tenant_id, site_id)


@router.get("/sites/{site_id}/content/schedule")
async def content_schedule(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await content.schedule_summary(db, tenant_id, site_id)


@router.get("/content/{content_id}")
async def get_content(content_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return content.content_out(await content.get(db, tenant_id, content_id), with_body=True)


@router.post("/content/{content_id}/draft")
async def submit_draft(content_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return content.content_out(await content.submit_draft(db, tenant_id, content_id, body, who), with_body=True)


@router.post("/content/{content_id}/approve")
async def approve_content(content_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return content.content_out(await content.approve(db, tenant_id, content_id, who))


@router.post("/content/{content_id}/schedule")
async def schedule_content(content_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return content.content_out(await content.schedule(db, tenant_id, content_id, body, who))


@router.post("/content/{content_id}/publish")
async def publish_content(content_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return content.content_out(await content.publish(db, tenant_id, content_id, who))


@router.post("/sites/{site_id}/pages/read")
async def read_page(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    """A page of the site as text (agents read it before rewriting a
    title, a description or the page itself). Only the site's own pages."""
    site = await get_site(db, tenant_id, site_id)
    page = await adapters.fetch_page(site, str(body.get("url") or ""))
    return {"url": page["url"], "title": page["title"], "meta_description": page["meta_description"], "h1": page["h1"],
            "h2": page["h2"], "words": page["words"], "text": page["text"][:20000], "images": page["images"][:50]}


# ── technical ─────────────────────────────────────────────────────

@router.get("/sites/{site_id}/technical/audits")
async def list_audits(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await technical.list_audits(db, tenant_id, site_id)


@router.post("/sites/{site_id}/technical/audits/run")
async def run_audit(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await technical.run_audit(db, await get_site(db, tenant_id, site_id), who)


@router.get("/sites/{site_id}/technical/issues")
async def list_issues(site_id: str, request: Request, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await technical.list_issues(db, tenant_id, site_id, dict(request.query_params))


@router.get("/sites/{site_id}/technical/fixable")
async def fixable_issues(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    site = await get_site(db, tenant_id, site_id)
    return [row(i) for i in await technical.fixable(db, site, who.startswith("ai:"))]


@router.post("/technical/issues/{issue_id}/fix")
async def fix_issue(issue_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await technical.fix(db, tenant_id, issue_id, body, who)


@router.post("/technical/issues/{issue_id}/approve")
async def approve_issue(issue_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await technical.approve(db, tenant_id, issue_id, who))


@router.post("/technical/issues/{issue_id}/dismiss")
async def dismiss_issue(issue_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await technical.dismiss(db, tenant_id, issue_id, who))


@router.post("/technical/schema/build")
async def schema_build(body: dict = JsonBody):
    return build_schema(body.get("schema_type") or "", body.get("entity") or {})


@router.post("/technical/schema/validate")
async def schema_validate(body: dict = JsonBody):
    problems = validate_schema(body.get("jsonld"))
    return {"valid": not problems, "problems": problems}


@router.post("/technical/redirects/resolve")
async def redirects_resolve(body: dict = JsonBody):
    return resolve_redirects(body.get("rules") or [])


# ── AI answers (GEO) ──────────────────────────────────────────────

@router.get("/sites/{site_id}/geo/prompts")
async def geo_prompts(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await geo.list_prompts(db, tenant_id, site_id)


@router.post("/sites/{site_id}/geo/prompts", status_code=201)
async def add_geo_prompt(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return row(await geo.add_prompt(db, tenant_id, site_id, body))


@router.patch("/geo/prompts/{prompt_id}")
async def toggle_geo_prompt(prompt_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return row(await geo.set_prompt_active(db, tenant_id, prompt_id, bool(body.get("active"))))


@router.get("/sites/{site_id}/geo/checks")
async def geo_checks(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await geo.list_checks(db, tenant_id, site_id)


@router.post("/sites/{site_id}/geo/checks", status_code=201)
async def record_geo_check(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await geo.record_check(db, tenant_id, site_id, body, who))


@router.post("/sites/{site_id}/geo/checks/run")
async def run_geo_checks(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await geo.run_checks(db, await get_site(db, tenant_id, site_id), who)


@router.get("/sites/{site_id}/geo/visibility")
async def geo_visibility(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await geo.visibility(db, tenant_id, site_id)


@router.get("/sites/{site_id}/geo/gaps")
async def geo_gaps(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await geo.gaps(db, tenant_id, site_id)


@router.post("/sites/{site_id}/geo/optimize")
async def geo_optimize(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await geo.optimize(db, await get_site(db, tenant_id, site_id), who)


@router.get("/geo/engines")
async def geo_engines():
    return list(GEO_ENGINES)


# ── links ─────────────────────────────────────────────────────────

@router.get("/sites/{site_id}/links/prospects")
async def list_prospects(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await links.list_prospects(db, tenant_id, site_id)


@router.post("/sites/{site_id}/links/prospects", status_code=201)
async def add_prospect(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await links.add_prospect(db, tenant_id, site_id, body, who))


@router.post("/sites/{site_id}/links/discover")
async def discover_prospects(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await links.discover(db, await get_site(db, tenant_id, site_id), who, body.get("target_domains"))


@router.post("/links/prospects/{prospect_id}/status")
async def prospect_status(prospect_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await links.set_status(db, tenant_id, prospect_id, body.get("status") or "", who))


@router.post("/links/prospects/{prospect_id}/outreach", status_code=201)
async def prospect_outreach(prospect_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await links.outreach(db, tenant_id, prospect_id, body, who))


@router.get("/sites/{site_id}/links/outreach")
async def list_outreach(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await links.list_outreach(db, tenant_id, site_id)


@router.get("/sites/{site_id}/links/backlinks")
async def list_backlinks(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await links.list_backlinks(db, tenant_id, site_id)


@router.get("/sites/{site_id}/authority")
async def site_authority(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await links.authority(db, tenant_id, site_id)


# ── conversion ────────────────────────────────────────────────────

@router.get("/sites/{site_id}/cro/findings")
async def cro_findings(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await cro.list_findings(db, tenant_id, site_id)


@router.post("/sites/{site_id}/cro/findings/run")
async def run_cro(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await cro.analyse(db, await get_site(db, tenant_id, site_id), who, body.get("url"))


@router.post("/cro/findings/{finding_id}/dismiss")
async def dismiss_finding(finding_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return row(await cro.dismiss_finding(db, tenant_id, finding_id))


@router.post("/cro/cta-strength")
async def cta_strength(body: dict = JsonBody):
    return cro.cta_strength(body.get("text") or "")


@router.get("/sites/{site_id}/cro/experiments")
async def list_experiments(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await cro.list_experiments(db, tenant_id, site_id)


@router.post("/sites/{site_id}/cro/experiments", status_code=201)
async def create_experiment(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await cro.create_experiment(db, tenant_id, site_id, body, who))


@router.post("/sites/{site_id}/cro/next-experiment")
async def next_experiment(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await cro.next_experiment(db, await get_site(db, tenant_id, site_id), who)


@router.post("/cro/experiments/{exp_id}/results")
async def experiment_results(exp_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return row(await cro.record_results(db, tenant_id, exp_id, body))


@router.post("/cro/experiments/{exp_id}/evaluate")
async def evaluate_experiment(exp_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await cro.evaluate(db, tenant_id, exp_id, who)


# ── programmatic ──────────────────────────────────────────────────

@router.get("/sites/{site_id}/programmatic/templates")
async def list_templates(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await pseo.list_templates(db, tenant_id, site_id)


@router.post("/sites/{site_id}/programmatic/templates", status_code=201)
async def create_template(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await pseo.create_template(db, tenant_id, site_id, body, who))


@router.post("/programmatic/templates/{template_id}/rows", status_code=201)
async def add_rows(template_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await pseo.add_rows(db, tenant_id, template_id, body.get("rows") or [], who)


@router.post("/programmatic/templates/{template_id}/preview")
async def preview_template(template_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await pseo.preview(db, tenant_id, template_id, body.get("row") or {})


@router.post("/sites/{site_id}/programmatic/generate")
async def generate_pages(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await pseo.generate(db, await get_site(db, tenant_id, site_id), who)


@router.post("/programmatic/templates/{template_id}/publish-batch")
async def publish_batch(template_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await pseo.publish_batch(db, tenant_id, template_id, body.get("limit") or 25, who)


@router.get("/sites/{site_id}/programmatic/pages")
async def list_pages(site_id: str, status: str | None = None, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await pseo.list_pages(db, tenant_id, site_id, status)


@router.post("/programmatic/pages/{page_id}/approve")
async def approve_page(page_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await pseo.approve_page(db, tenant_id, page_id, who))


# ── local ─────────────────────────────────────────────────────────

@router.get("/sites/{site_id}/local/locations")
async def list_locations(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await local.list_locations(db, tenant_id, site_id)


@router.post("/sites/{site_id}/local/locations", status_code=201)
async def create_location(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return local.location_out(await local.create_location(db, tenant_id, site_id, body, who))


@router.patch("/local/locations/{location_id}")
async def update_location(location_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return local.location_out(await local.update_location(db, tenant_id, location_id, body, who))


@router.post("/local/locations/{location_id}/audit")
async def audit_location(location_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return local.location_out(await local.audit(db, tenant_id, location_id, who))


@router.post("/sites/{site_id}/local/audit")
async def audit_locations(site_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await local.audit_site(db, await get_site(db, tenant_id, site_id), who)


@router.post("/local/locations/{location_id}/profile")
async def change_profile(location_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return await local.apply_change(db, tenant_id, location_id, body, who)


@router.get("/local/locations/{location_id}/nap")
async def location_nap(location_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await local.nap(db, tenant_id, location_id)


@router.get("/sites/{site_id}/local/keywords")
async def local_keywords(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await local.keywords(db, tenant_id, site_id)


@router.get("/sites/{site_id}/local/rank")
async def local_rank(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await local.rank(db, tenant_id, site_id)


# ── reports and requests ──────────────────────────────────────────

@router.get("/sites/{site_id}/reports")
async def list_reports(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await reports.list_reports(db, tenant_id, site_id)


@router.post("/sites/{site_id}/reports/generate", status_code=201)
async def generate_report(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    report = await reports.generate(db, await get_site(db, tenant_id, site_id), body.get("period") or "weekly", who)
    return row(report, exclude=("summary",))


@router.get("/reports/{report_id}")
async def get_report(report_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await reports.get_report(db, tenant_id, report_id)


@router.post("/reports/{report_id}/narrative")
async def report_narrative(report_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await reports.set_narrative(db, tenant_id, report_id, body.get("narrative") or "")


@router.post("/reports/{report_id}/send")
async def send_report(report_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    report = await get_owned(db, SeoReport, tenant_id, report_id, "report")
    site = await get_site(db, tenant_id, report.site_id)
    return {"sent": await reports.send(db, site, report)}


@router.get("/sites/{site_id}/requests")
async def list_requests(site_id: str, tenant_id: str = Tenant, db: AsyncSession = Db):
    return await intake.list_requests(db, tenant_id, site_id)


async def _receive_and_convert(db: AsyncSession, tenant_id: str, site, body: dict) -> dict:
    request = await intake.receive(db, site, body)
    try:
        await agents_bridge.intake(tenant_id, str(request.id))
    except SeoError:
        pass  # stays "received"; the intake agent picks it up from the event
    await db.refresh(request)
    return row(request)


@router.post("/sites/{site_id}/requests/email", status_code=201)
async def email_request(site_id: str, body: dict = JsonBody, tenant_id: str = Tenant, db: AsyncSession = Db):
    """A request as if emailed (the console's test form, or a mail relay
    inside the platform)."""
    return await _receive_and_convert(db, tenant_id, await get_site(db, tenant_id, site_id), body)


@router.post("/requests/{request_id}/convert")
async def convert_request(request_id: str, tenant_id: str = Tenant, who: str = Actor, db: AsyncSession = Db):
    return row(await intake.convert(db, tenant_id, request_id, who))


# ── public inbound mail webhook ───────────────────────────────────

@public_router.post("/inbound/{site_id}", status_code=201)
async def inbound_email(site_id: str, request: Request, token: str = Query(default=""), db: AsyncSession = Db):
    """For an email provider's inbound webhook (SendGrid Inbound Parse,
    Mailgun routes, or JSON). The token is the 'seo_inbox' integration's."""
    site = (await db.execute(select(SeoSite).where(SeoSite.id == _uuid_or_none(site_id)))).scalar_one_or_none()
    creds = await tenant_integrations.get(site.tenant_id, "seo_inbox") if site else None
    if site is None or not creds or not creds.get("token") or not hmac.compare_digest(str(creds["token"]), token):
        return JSONResponse(status_code=404, content={"detail": "not found"})
    if "json" in request.headers.get("content-type", ""):
        data = await request.json()
    else:
        form = await request.form()
        data = {"sender": form.get("sender") or form.get("from"), "subject": form.get("subject"),
                "body": form.get("body-plain") or form.get("stripped-text") or form.get("text")}
    return await _receive_and_convert(db, site.tenant_id, site, {k: (str(v) if v is not None else "") for k, v in data.items()})


def _uuid_or_none(value: str):
    try:
        return uuid.UUID(value)
    except ValueError:
        return None

