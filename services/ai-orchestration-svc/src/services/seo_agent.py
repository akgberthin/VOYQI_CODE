"""
Growth / SEO agents — the fourteen agents of the marketing command
center's Growth module, one module, one shared context.

How they work: the SEO engine in marketing-svc owns the data, the task
queue and every rule (autonomy per site, caps, the link-scheme gate,
the content guardrail, the fix policy). The autopilot agent asks it for
the plan each cycle and hands every task the plan allows to the agent
that owns that task type. That agent claims the task (the engine
re-checks the plan), does the work through the engine's API as itself,
and reports the result. Each run is written to the decision log.

Where judgment or writing is needed (articles, titles and descriptions,
outreach emails, profile text, strategy and report narratives) the
agent asks Claude with the tenant's own key. Without a key it falls back
to deterministic templates — and anything written from a template is
held for a person instead of going live.

Pausing an agent in the registry stops it: its tasks stay queued and
the cycle reports them held.
"""
import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services.seo_tools import SeoApi, SeoApiError

logger = logging.getLogger("ai-orchestration-svc.seo")

AGENTS: list[dict] = [
    {"key": "seo_strategy_agent", "name": "SEO Strategy Agent",
     "description": "Builds and refreshes the SEO strategy from real data, prioritised by expected impact per effort.",
     "task_types": ["strategy_refresh", "custom"], "events": ["seo.site_registered", "seo.task.custom", "seo.task.strategy_refresh"],
     "live_hint": "Generate a fresh strategy for this site and explain the three highest-priority initiatives."},
    {"key": "keyword_research_agent", "name": "Keyword Research Agent",
     "description": "Finds high-intent keywords, competitor gaps, trends and the questions buyers ask.",
     "task_types": ["keyword_research"], "events": ["seo.task.keyword_research"],
     "live_hint": "Refresh keyword data and report the best opportunities."},
    {"key": "search_intelligence_agent", "name": "Search Intelligence Agent",
     "description": "Researches search results, competitors, Reddit, news, YouTube and other engines to inform strategy and content.",
     "task_types": ["research_digest", "competitor_research"], "events": ["seo.task.competitor_research", "seo.task.research_digest"],
     "live_hint": "Gather and digest the latest market research."},
    {"key": "seo_content_agent", "name": "SEO Content Agent",
     "description": "Plans, writes, refreshes, schedules and publishes on-brand SEO content.",
     "task_types": ["content_create", "content_refresh"], "events": ["seo.task.content_create", "seo.task.content_refresh"],
     "live_hint": "Write the content now. Start with the brief."},
    {"key": "technical_seo_agent", "name": "Technical SEO Agent",
     "description": "Audits speed, crawl and indexing, implements structured data, fixes redirects and applies safe fixes automatically.",
     "task_types": ["technical_audit", "technical_fix", "schema_implementation", "redirect_fix"],
     "events": ["seo.audit_completed", "seo.task.redirect_fix", "seo.task.schema_implementation", "seo.task.technical_audit",
                "seo.task.technical_fix"],
     "live_hint": "Work through the open technical issues, highest severity first."},
    {"key": "geo_agent", "name": "GEO Agent",
     "description": "Tracks and improves how the brand appears in ChatGPT, Perplexity, Claude and Google AI answers.",
     "task_types": ["geo_check", "geo_optimize"], "events": ["seo.task.geo_check", "seo.task.geo_optimize"],
     "live_hint": "Check AI-answer visibility, then queue the optimisation work."},
    {"key": "link_building_agent", "name": "Link Building Agent",
     "description": "Finds relevant link prospects, runs compliant outreach and tracks authority.",
     "task_types": ["link_prospect", "link_outreach"], "events": ["seo.task.link_outreach", "seo.task.link_prospect"],
     "live_hint": "Process link prospects or outreach as the task requires."},
    {"key": "cro_agent", "name": "CRO Agent",
     "description": "Finds conversion problems in layouts and calls to action and runs experiments.",
     "task_types": ["cro_analysis", "cro_experiment"], "events": ["seo.task.cro_analysis", "seo.task.cro_experiment"],
     "live_hint": "Review conversion or start the next experiment."},
    {"key": "programmatic_seo_agent", "name": "Programmatic SEO Agent",
     "description": "Generates data-driven landing pages at scale, behind the thin-content gate.",
     "task_types": ["pseo_batch"], "events": ["seo.task.pseo_batch"], "live_hint": "Generate and publish programmatic pages."},
    {"key": "local_seo_agent", "name": "Local SEO Agent",
     "description": "Optimises Google Business Profiles and local-pack visibility across locations.",
     "task_types": ["local_audit", "gbp_optimize"], "events": ["seo.task.gbp_optimize", "seo.task.local_audit"],
     "live_hint": "Audit or improve the Business Profiles."},
    {"key": "seo_reporting_agent", "name": "SEO Reporting Agent",
     "description": "Produces the recurring progress reports and keeps the live activity feed meaningful.",
     "task_types": ["report"], "events": ["seo.task.report"], "live_hint": "Generate the progress report."},
    {"key": "seo_autopilot_agent", "name": "SEO Autopilot Agent",
     "description": "Runs the whole programme continuously: replans, dispatches each eligible task to its owning agent, and records every cycle.",
     "task_types": [], "events": ["seo.autopilot_tick"], "live_hint": ""},
    {"key": "seo_task_intake_agent", "name": "Task Intake Agent",
     "description": "Turns authorised custom requests sent by email into queued tasks.",
     "task_types": [], "events": ["seo.email_received"], "live_hint": ""},
    {"key": "seo_integration_agent", "name": "Integration Agent",
     "description": "Keeps the CMS, Search Console, analytics, website builder and other connections healthy and their data flowing.",
     "task_types": ["integration_sync"], "events": ["seo.integration_tested", "seo.task.integration_sync"],
     "live_hint": "Sync every connected source."},
]
AGENT_KEYS = [a["key"] for a in AGENTS]
AUTOPILOT, INTAKE = "seo_autopilot_agent", "seo_task_intake_agent"
OWNER = {t: a["key"] for a in AGENTS for t in a["task_types"]}

BRAND_RULES = """Brand rules: VOYQI's voice is premium, confident and plain-spoken. Never use absolute claims \
("guaranteed", "risk-free", "no risk", "best in the world", "unbeatable", "#1 rated", "number one", "100% satisfaction", \
"can't lose", "free money", "cure"). If you mention financing, APR, credit or monthly payments, say it is subject to \
credit approval. Never invent statistics, prices, reviews or sources: use only facts given to you."""

SEO_WRITER_SYSTEM_PROMPT = f"""You are the SEO Content Agent for a growth team. You write search-optimised web pages \
that genuinely answer what the searcher wants. {BRAND_RULES}

You get a brief: the target keyword, intent, format, angle, what the page must include, secondary keywords, questions \
to answer, internal pages to link and a target length. Write the page in Markdown: one H1 containing the keyword, clear \
H2 sections following the outline, the keyword in the first paragraph and used naturally (not stuffed), each listed \
question answered under its own H2 phrased as the question, and at least two links to the internal pages given, written \
as [text](url). Keep sentences short. Do not repeat yourself.

Answer with JSON only: {{"title": "30-65 characters, contains the keyword", "meta_description": "70-160 characters", \
"body_markdown": "..."}}"""

SEO_METADATA_SYSTEM_PROMPT = f"""You are the Technical SEO Agent writing page metadata. {BRAND_RULES}
Given a page's text, write what is asked: a title (30-60 characters, the page's main topic first, brand last after \
" | "), a meta description (120-155 characters, says what the page offers and invites the click) or image alt texts \
(describe each image briefly for someone who cannot see it, using the file name and page context; no "image of"). \
Answer with JSON only, using exactly the keys you are asked for."""

SEO_OUTREACH_SYSTEM_PROMPT = f"""You are the Link Building Agent writing one outreach email to a website editor. {BRAND_RULES}
Be brief (under 140 words), specific to their site, and useful to their readers: suggest the page of ours that would \
help them and why. Never offer payment, a link exchange or anything in return for a link. End with a line that lets \
them opt out, for example: "If you'd rather not hear from us, reply 'unsubscribe' and we won't email again."
Answer with JSON only: {{"subject": "...", "body": "..."}}"""

SEO_LOCAL_SYSTEM_PROMPT = f"""You are the Local SEO Agent writing for a Google Business Profile. {BRAND_RULES}
Write what is asked: a business description (250-700 characters: what the location offers and where, in plain words) \
or an update post (60-280 characters: something current and useful at that location, with a reason to visit). \
Answer with JSON only: {{"text": "..."}}"""

SEO_STRATEGY_SYSTEM_PROMPT = f"""You are the SEO Strategy Agent. You explain a data-driven SEO strategy to a busy \
marketing lead. {BRAND_RULES}
In under 220 words: what matters most right now and why, the three highest-priority initiatives in order with the \
evidence behind each, and what is blocked or needs a person. Plain text, no Markdown headings."""

SEO_REPORT_SYSTEM_PROMPT = f"""You are the SEO Reporting Agent. Turn the figures you are given into a short progress \
report for a marketing lead. {BRAND_RULES}
Under 250 words, plain text: what changed (with the numbers), what the agents did, and what needs a person. Do not \
add numbers that are not in the data."""

TECHNICAL_FIX_TYPES = {
    "technical_fix": None,  # every fixable issue
    "schema_implementation": {"add_schema"},
    "redirect_fix": {"add_redirect", "fix_redirect_chain", "make_redirect_permanent"},
}
FIXES_PER_TASK = 15


@dataclass
class Ctx:
    api: SeoApi
    task: dict
    site: dict
    client: AnthropicAgentClient
    notes: list[str] = field(default_factory=list)
    live: bool = False

    @property
    def sid(self) -> str:
        return self.task["site_id"]

    @property
    def params(self) -> dict:
        return self.task.get("params") or {}


# ── asking Claude ─────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _ask(ctx: Ctx, system_prompt: str, user: str, max_tokens: int = 2000, as_json: bool = True) -> Any:
    """Claude's answer, or None without a key or on any failure (the
    caller then uses its template)."""
    if not ctx.client.is_configured:
        return None
    try:
        text = await asyncio.to_thread(ctx.client.complete, system_prompt, user, max_tokens)
    except Exception as exc:  # an API failure falls back to the template
        ctx.notes.append(f"Claude unavailable ({type(exc).__name__}); used the template")
        return None
    ctx.live = True
    return _parse_json(text) if as_json else (text or "").strip() or None


def _brand(site: dict) -> dict:
    p = site.get("business_profile") or {}
    return {"name": site["name"], "domain": site["domain"], "description": p.get("description"), "positioning": p.get("positioning"),
            "products": p.get("products"), "markets": p.get("markets")}


# ── deterministic templates (used without a key; never published unseen) ──

def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def template_article(brief: dict, site: dict) -> dict:
    kw = brief["keyword"]
    brand = site["name"]
    profile = site.get("business_profile") or {}
    about = (profile.get("description") or f"{brand} offers {', '.join(profile.get('products') or [kw])}.").rstrip(".")
    title = f"{_cap(kw)}: {'a clear guide' if brief['intent'] == 'informational' else 'what to know before you choose'}"[:65]
    meta = f"{_cap(kw)}, explained: {brief['angle']}. See how {brand} approaches it and who it suits."[:158]
    links = [li["url"] for li in brief.get("internal_link_targets") or []][:3] or [f"https://{site['domain']}/"]
    parts = [f"# {title}", "",
             f"Looking into {kw}? This page covers what it is, how it works and what to check before you decide. {about}.", ""]
    for item in brief.get("must_include") or []:
        parts += [f"## {_cap(item)}", "",
                  f"When you compare options for {kw}, {item} is one of the first things to look at. "
                  f"Ask each provider to spell it out in writing, and check how it fits the way you actually travel.", ""]
    for q in brief.get("questions_to_answer") or []:
        parts += [f"## {_cap(q.rstrip('?'))}?", "",
                  f"It depends on your plan and how often you need a vehicle. {brand} sets out the details for each plan "
                  f"on its plans page, so you can compare before you commit.", ""]
    parts += ["## Next steps", "", "Read more: " + ", ".join(f"[{u.rstrip('/').rsplit('/', 1)[-1] or site['domain']}]({u})" for u in links) + ".",
              "", "## Frequently asked questions", "", f"**What should I check first?** What is included in the price for {kw}, "
              "and what costs extra.", ""]
    return {"title": title, "meta_description": meta, "body_markdown": "\n".join(parts)}


def template_metadata(page: dict, site: dict) -> dict:
    main = (page.get("h1") or [None])[0] or page.get("title") or page["url"].rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    title = f"{_cap(main.strip())} | {site['name']}"
    if len(title) > 60:
        title = _cap(main.strip())[:60]
    sentences = re.split(r"(?<=[.!?])\s+", page.get("text") or "")
    meta = ""
    for s in sentences:
        if len(meta) + len(s) > 155:
            break
        meta = f"{meta} {s}".strip()
    return {"title": title, "meta_description": meta or f"{_cap(main)} from {site['name']}."[:155]}


def template_alt(images: list[str], page: dict) -> dict:
    topic = (page.get("h1") or [page.get("title") or ""])[0]
    out = {}
    for src in images:
        words = re.sub(r"[-_]+", " ", re.sub(r"\.[a-z0-9]+$", "", src.rsplit("/", 1)[-1], flags=re.I)).strip()
        words = re.sub(r"\b\d+x\d+\b|\b\d{3,}\b", "", words).strip()
        out[src] = _cap(words or topic or "Illustration")[:120]
    return out


def template_outreach(prospect: dict, site: dict, page_url: str) -> dict:
    return {"subject": f"A resource for {prospect['domain']} readers",
            "body": (f"Hi,\n\nI read {prospect['domain']} and thought this guide from {site['name']} could be useful to your readers: "
                     f"{page_url}\n\nIt covers the practical questions people ask before choosing, in plain language. If it fits a page "
                     f"of yours, feel free to reference it.\n\nThanks for your time.\n\nIf you'd rather not hear from us, reply "
                     f"'unsubscribe' and we won't email again.")}


def template_description(loc: dict, site: dict) -> str | None:
    profile = site.get("business_profile") or {}
    city = (loc.get("address") or {}).get("city", "")
    services = ", ".join(loc.get("services") or profile.get("products") or [])
    text = (f"{loc['name']} is {site['name']}'s location{f' in {city}' if city else ''}. "
            f"{(profile.get('description') or '').rstrip('.')}. "
            f"{f'Here you can arrange {services}. ' if services else ''}"
            f"Our team helps you pick the right plan, collect your vehicle and get on the road. "
            f"Visit {loc.get('website') or site['domain']} to see plans and opening hours.")
    text = re.sub(r"\s+\.", ".", re.sub(r"\s+", " ", text)).strip()
    return text if len(text) >= 250 else None


# ── handlers: one per task type ───────────────────────────────────

Handler = Callable[[Ctx], Awaitable[dict]]


async def strategy_refresh(ctx: Ctx) -> dict:
    result = await ctx.api.post(f"/sites/{ctx.sid}/strategy/generate")
    strategy = result["strategy"]
    top = strategy["initiatives"][:3]
    narrative = await _ask(ctx, SEO_STRATEGY_SYSTEM_PROMPT, json.dumps({"site": _brand(ctx.site), "initiatives": strategy["initiatives"],
                                                                        "inputs": strategy["inputs_snapshot"]}, default=str)[:30000],
                           900, as_json=False)
    if not narrative:
        narrative = " ".join(f"{i + 1}. {it['title']} — {it['rationale']}" for i, it in enumerate(top)) or "Nothing urgent: keep measuring."
    await ctx.api.post(f"/strategy/{strategy['id']}/narrative", {"narrative": narrative})
    return {"summary": f"strategy v{strategy['version']}: {len(strategy['initiatives'])} initiatives, {len(result['tasks_created'])} new task(s)",
            "strategy_id": strategy["id"]}


async def custom(ctx: Ctx) -> dict:
    """A person's custom request (always approved by a person first):
    the strategy agent turns it into a plan the team can act on."""
    plan = await _ask(ctx, SEO_STRATEGY_SYSTEM_PROMPT, f"Custom request for {ctx.site['domain']}: {ctx.task['title']}\n"
                      f"{ctx.task.get('description') or ''}\nParams: {json.dumps(ctx.params)}\nWrite a short, concrete plan.", 900, as_json=False)
    return {"summary": "plan written for the custom request" if plan else "custom request recorded for the team",
            "plan": plan or "No AI writer is configured; the request is recorded for a person to plan."}


async def keyword_research(ctx: Ctx) -> dict:
    counts = await ctx.api.post(f"/sites/{ctx.sid}/keywords/sync")
    return {"summary": ", ".join(f"{v} {k}" for k, v in counts.items()), "counts": counts}


async def research_digest(ctx: Ctx) -> dict:
    counts = await ctx.api.post(f"/sites/{ctx.sid}/research/run")
    digest = await ctx.api.get(f"/sites/{ctx.sid}/research/digest")
    return {"summary": f"{digest['items']} research items; top themes: {', '.join(t['theme'] for t in digest['themes'][:4]) or 'none yet'}",
            "counts": counts}


async def competitor_research(ctx: Ctx) -> dict:
    counts = await ctx.api.post(f"/sites/{ctx.sid}/research/run", {"sources": ["competitor", "serp", "multi_engine"]})
    return {"summary": ", ".join(f"{k}: {v}" for k, v in counts.items()), "counts": counts}


async def _write_and_submit(ctx: Ctx, item: dict, brief: dict, existing_page: dict | None = None) -> dict:
    payload = {"brief": brief, "brand": _brand(ctx.site)}
    if existing_page:
        payload["current_page"] = {"url": existing_page["url"], "title": existing_page["title"], "text": existing_page["text"][:12000]}
        payload["instruction"] = ("Rewrite this existing page so it serves the brief better. Keep what is accurate and useful, "
                                  "fix what is thin or outdated, keep the same topic.")
    draft = await _ask(ctx, SEO_WRITER_SYSTEM_PROMPT, json.dumps(payload, default=str), 8000)
    written_by = "claude"
    if not draft or not all(draft.get(k) for k in ("title", "meta_description", "body_markdown")):
        if existing_page:
            raise SeoApiError(409, "refreshing a live page needs the AI writer (an Anthropic key in the super admin console); "
                                   "the template writer is never used to replace a live page", "provider_not_configured")
        draft, written_by = template_article(brief, ctx.site), "template"
    submitted = await ctx.api.post(f"/content/{item['id']}/draft", {**draft, "written_by": written_by})
    if submitted["status"] == "needs_revision" and written_by == "claude":
        revised = await _ask(ctx, SEO_WRITER_SYSTEM_PROMPT, json.dumps({
            **payload, "previous_draft": draft, "fix_these": submitted["quality_findings"],
            "instruction": "Revise the previous draft so every finding is fixed."}, default=str), 8000)
        if revised and all(revised.get(k) for k in ("title", "meta_description", "body_markdown")):
            submitted = await ctx.api.post(f"/content/{item['id']}/draft", {**revised, "written_by": "claude"})
    if submitted["status"] == "approved":
        submitted = await ctx.api.post(f"/content/{item['id']}/schedule")
    return submitted


async def content_create(ctx: Ctx) -> dict:
    keyword = (ctx.params.get("keyword") or ctx.params.get("topic") or "").strip()
    if not keyword:
        raise SeoApiError(422, "the task names no keyword or topic to write about", "invalid")
    brief = await ctx.api.post(f"/sites/{ctx.sid}/content/brief", {"keyword": keyword, "kind": ctx.params.get("kind")})
    item = await ctx.api.post(f"/sites/{ctx.sid}/content", {"kind": brief["kind"], "target_keyword": keyword, "brief": brief})
    if item["status"] in ("published", "scheduled", "held_for_review"):
        return {"summary": f"'{keyword}' is already {item['status'].replace('_', ' ')}", "content_id": item["id"]}
    result = await _write_and_submit(ctx, item, brief)
    return {"summary": f"'{result['title'][:70]}' — {result['status'].replace('_', ' ')} (SEO score {result['seo_score']})",
            "content_id": item["id"], "status": result["status"]}


async def content_refresh(ctx: Ctx) -> dict:
    url, keyword = ctx.params.get("url"), (ctx.params.get("keyword") or "").strip()
    if not url:
        raise SeoApiError(422, "the task names no page to refresh", "invalid")
    if not ctx.client.is_configured:
        raise SeoApiError(409, "refreshing a live page needs the AI writer (an Anthropic key in the super admin console); "
                               "the template writer is never used to replace a live page", "provider_not_configured")
    page = await ctx.api.post(f"/sites/{ctx.sid}/pages/read", {"url": url})
    keyword = keyword or ((page.get("h1") or [page.get("title")])[0] or "").lower()
    brief = await ctx.api.post(f"/sites/{ctx.sid}/content/brief", {"keyword": keyword})
    item = await ctx.api.post(f"/sites/{ctx.sid}/content", {"kind": "refresh", "target_keyword": keyword, "brief": brief, "refresh_of_url": url})
    result = await _write_and_submit(ctx, item, brief, page)
    return {"summary": f"refresh of {url}: {result['status'].replace('_', ' ')} (SEO score {result['seo_score']})", "content_id": item["id"]}


async def technical_audit(ctx: Ctx) -> dict:
    audit = await ctx.api.post(f"/sites/{ctx.sid}/technical/audits/run")
    return {"summary": f"{audit['pages_scanned']} pages: health {audit['health_score']}/100, {audit['new']} new issue(s), "
                       f"{audit['resolved']} resolved", "audit_id": audit["id"]}


async def _fix_body(ctx: Ctx, issue: dict) -> dict | None:
    plan = issue["fix_plan"]
    params = plan.get("params") or {}
    if not params.get("needs_generation"):
        return {}
    page = await ctx.api.post(f"/sites/{ctx.sid}/pages/read", {"url": params.get("url") or issue["url"]})
    if plan["fix_type"] == "add_alt_text":
        images = params.get("images") or []
        answer = await _ask(ctx, SEO_METADATA_SYSTEM_PROMPT, json.dumps({"want": "alt_texts as {image url: alt text}", "images": images,
                                                                         "page": {"title": page["title"], "text": page["text"][:4000]}}))
        alt = (answer or {}).get("alt_texts") if isinstance((answer or {}).get("alt_texts"), dict) else None
        return {"alt_texts": alt or template_alt(images, page)}
    key = "title" if plan["fix_type"] == "update_title" else "meta_description"
    answer = await _ask(ctx, SEO_METADATA_SYSTEM_PROMPT, json.dumps({"want": key, "brand": ctx.site["name"],
                                                                     "page": {"url": page["url"], "h1": page["h1"], "text": page["text"][:6000]}}))
    value = (answer or {}).get(key) or template_metadata(page, ctx.site)[key]
    return {"value": value}


async def technical_fix(ctx: Ctx) -> dict:
    allowed = TECHNICAL_FIX_TYPES.get(ctx.task["type"])
    issues = await ctx.api.get(f"/sites/{ctx.sid}/technical/fixable")
    if allowed is not None:
        issues = [i for i in issues if i["fix_plan"]["fix_type"] in allowed]
    fixed, skipped = [], []
    for issue in issues[:FIXES_PER_TASK]:
        try:
            body = await _fix_body(ctx, issue)
            await ctx.api.post(f"/technical/issues/{issue['id']}/fix", body)
            fixed.append(f"{issue['code']} on {issue['url']}")
        except SeoApiError as exc:
            if exc.status == 409 and "cap" in exc.detail:
                skipped.append(f"stopped: {exc.detail}")
                break
            if exc.needs_integration:
                raise
            skipped.append(f"{issue['code']} on {issue['url']}: {exc.detail}")
    if not issues:
        return {"summary": "no issue an agent may fix right now"}
    if not fixed and skipped:
        raise SeoApiError(409, "no fix could be applied: " + "; ".join(skipped[:3]))
    return {"summary": f"{len(fixed)} fix(es) applied" + (f", {len(skipped)} skipped" if skipped else ""), "fixed": fixed, "skipped": skipped}


async def geo_check(ctx: Ctx) -> dict:
    result = await ctx.api.post(f"/sites/{ctx.sid}/geo/checks/run")
    vis = await ctx.api.get(f"/sites/{ctx.sid}/geo/visibility")
    gaps = await ctx.api.get(f"/sites/{ctx.sid}/geo/gaps")
    if gaps["gaps"]:
        await ctx.api.post(f"/sites/{ctx.sid}/tasks", {"type": "geo_optimize", "title": "Turn AI-answer gaps into content and citations",
                                                       "params": {}, "impact": 60, "effort": 2, "confidence": 0.6})
    return {"summary": f"{result['checks']} answers checked; visibility {vis['overall']['score']}/100, {len(gaps['gaps'])} gap(s)",
            "errors": result.get("errors")}


async def geo_optimize(ctx: Ctx) -> dict:
    result = await ctx.api.post(f"/sites/{ctx.sid}/geo/optimize")
    return {"summary": f"{len(result['tasks_queued'])} task(s) queued from {result['gaps']} gap(s)", "tasks": result["tasks_queued"]}


async def link_prospect(ctx: Ctx) -> dict:
    result = await ctx.api.post(f"/sites/{ctx.sid}/links/discover", {"target_domains": ctx.params.get("target_domains")})
    return {"summary": f"{result['added']} prospect(s) added, {result['blocked']} blocked by the link-scheme gate"}


async def link_outreach(ctx: Ctx) -> dict:
    prospects = [p for p in await ctx.api.get(f"/sites/{ctx.sid}/links/prospects")
                 if p["status"] == "approved_for_outreach" and p.get("contact_email")]
    content = [c for c in await ctx.api.get(f"/sites/{ctx.sid}/content", status="published") if c.get("published_url")]
    page_url = content[0]["published_url"] if content else f"https://{ctx.site['domain']}/"
    sent, held = [], []
    for p in prospects[:10]:
        email = await _ask(ctx, SEO_OUTREACH_SYSTEM_PROMPT, json.dumps({
            "their_site": p["domain"], "their_notes": p.get("notes"), "our_brand": _brand(ctx.site),
            "our_pages": [{"title": c["title"], "url": c["published_url"]} for c in content[:5]] or [{"title": ctx.site["name"], "url": page_url}]}))
        if not email or not email.get("subject") or not email.get("body"):
            email = template_outreach(p, ctx.site, page_url)
        try:
            result = await ctx.api.post(f"/links/prospects/{p['id']}/outreach", {"subject": email["subject"], "body": email["body"]})
        except SeoApiError as exc:
            if exc.status == 409 and "cap" in exc.detail:
                break
            held.append(f"{p['domain']}: {exc.detail}")
            continue
        (sent if result["status"] == "sent" else held).append(f"{p['domain']}: {result['status']}")
    if not prospects:
        return {"summary": "no prospect is approved for outreach (a person approves prospects)"}
    return {"summary": f"{len(sent)} email(s) sent, {len(held)} held", "sent": sent, "held": held}


async def cro_analysis(ctx: Ctx) -> dict:
    result = await ctx.api.post(f"/sites/{ctx.sid}/cro/findings/run", {"url": ctx.params.get("url")})
    return {"summary": f"{result['pages_reviewed']} page(s) reviewed, {result['new_findings']} new finding(s)"}


async def cro_experiment(ctx: Ctx) -> dict:
    decided = []
    for exp in await ctx.api.get(f"/sites/{ctx.sid}/cro/experiments"):
        if exp["status"] == "running":
            result = await ctx.api.post(f"/cro/experiments/{exp['id']}/evaluate")
            if result["decision"] not in ("keep_running", "insufficient_data"):
                decided.append(f"{exp['page_url']}: {result['decision']}")
    started = await ctx.api.post(f"/sites/{ctx.sid}/cro/next-experiment")
    return {"summary": (f"experiment started on {started['page_url']}" if started.get("started") else started.get("reason", "no new experiment"))
            + (f"; {len(decided)} concluded" if decided else ""), "concluded": decided}


async def pseo_batch(ctx: Ctx) -> dict:
    generated = await ctx.api.post(f"/sites/{ctx.sid}/programmatic/generate")
    published = 0
    for tpl in await ctx.api.get(f"/sites/{ctx.sid}/programmatic/templates"):
        try:
            result = await ctx.api.post(f"/programmatic/templates/{tpl['id']}/publish-batch", {"limit": 25})
            published += result["published"]
        except SeoApiError as exc:
            if exc.status == 409 and "cap" in exc.detail:
                break
            raise
    return {"summary": f"{generated['generated']} generated ({generated['held']} held for review), {published} published"}


async def local_audit(ctx: Ctx) -> dict:
    scores = await ctx.api.post(f"/sites/{ctx.sid}/local/audit")
    return {"summary": ", ".join(f"{k}: {v}/100" for k, v in scores.items()) or "no locations yet"}


async def gbp_optimize(ctx: Ctx) -> dict:
    locations = await ctx.api.get(f"/sites/{ctx.sid}/local/locations")
    wanted = ctx.params.get("location_id")
    done = []
    for loc in locations:
        if wanted and loc["id"] != wanted:
            continue
        # A change already waiting for a person is not made again.
        recent = {p["item"] for p in (loc.get("profile") or {}).get("pending") or []
                  if p.get("at", "") >= (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()}
        missing = {m["item"] for m in loc.get("gbp_missing") or [] if m.get("agent_can_do")} - recent
        if "description" in missing:
            answer = await _ask(ctx, SEO_LOCAL_SYSTEM_PROMPT, json.dumps({"want": "business description", "location": loc,
                                                                          "brand": _brand(ctx.site)}, default=str))
            text = (answer or {}).get("text")
            text = text if text and len(text) >= 250 else template_description(loc, ctx.site)
            if text:
                result = await ctx.api.post(f"/local/locations/{loc['id']}/profile", {"item": "description", "value": text})
                done.append(f"{loc['name']}: description ({result['detail']})")
        if "services" in missing:
            services = (ctx.site.get("business_profile") or {}).get("products") or []
            if len(services) >= 2:
                result = await ctx.api.post(f"/local/locations/{loc['id']}/profile", {"item": "services", "value": services})
                done.append(f"{loc['name']}: services ({result['detail']})")
        if "posts" in missing:
            answer = await _ask(ctx, SEO_LOCAL_SYSTEM_PROMPT, json.dumps({"want": "update post", "location": loc, "brand": _brand(ctx.site)}, default=str))
            text = (answer or {}).get("text") or (f"This week at {loc['name']}: plans are available to start right away. "
                                                  f"See what's included at {loc.get('website') or ctx.site['domain']}.")
            result = await ctx.api.post(f"/local/locations/{loc['id']}/profile", {"item": "posts", "value": text})
            done.append(f"{loc['name']}: update post ({result['detail']})")
    return {"summary": "; ".join(done) or "nothing an agent can improve on these profiles", "changes": done}


async def report(ctx: Ctx) -> dict:
    created = await ctx.api.post(f"/sites/{ctx.sid}/reports/generate", {"period": ctx.params.get("period") or ctx.site.get("report_frequency") or "weekly"})
    full = await ctx.api.get(f"/reports/{created['id']}")
    narrative = await _ask(ctx, SEO_REPORT_SYSTEM_PROMPT, json.dumps({"site": ctx.site["domain"], "period": full["period"],
                                                                      "figures": full["summary"], "draft": full["narrative"]}, default=str)[:30000],
                           1200, as_json=False)
    if narrative:
        await ctx.api.post(f"/reports/{created['id']}/narrative", {"narrative": narrative})
    sent = (await ctx.api.post(f"/reports/{created['id']}/send"))["sent"] if ctx.site.get("report_recipients") else 0
    return {"summary": f"{full['period']} report generated" + (f", emailed to {sent}" if sent else ""), "report_id": created["id"]}


async def integration_sync(ctx: Ctx) -> dict:
    results = await ctx.api.post(f"/sites/{ctx.sid}/integrations/sync")
    if not results:
        return {"summary": "no measurement source is connected"}
    return {"summary": "; ".join(f"{k}: {v}" for k, v in results.items()), "results": results}


HANDLERS: dict[str, Handler] = {
    "strategy_refresh": strategy_refresh, "custom": custom, "keyword_research": keyword_research,
    "research_digest": research_digest, "competitor_research": competitor_research, "content_create": content_create,
    "content_refresh": content_refresh, "technical_audit": technical_audit, "technical_fix": technical_fix,
    "schema_implementation": technical_fix, "redirect_fix": technical_fix, "geo_check": geo_check, "geo_optimize": geo_optimize,
    "link_prospect": link_prospect, "link_outreach": link_outreach, "cro_analysis": cro_analysis, "cro_experiment": cro_experiment,
    "pseo_batch": pseo_batch, "local_audit": local_audit, "gbp_optimize": gbp_optimize, "report": report,
    "integration_sync": integration_sync,
}


# ── running tasks, cycles and intake ──────────────────────────────

async def agent_status(db: AsyncSession, tenant_id: str, agent_key: str) -> AgentStatus:
    row = (await db.execute(select(Agent).where(Agent.key == agent_key, Agent.tenant_id == tenant_id))).scalar_one_or_none()
    return row.status if row else AgentStatus.ACTIVE


async def _log(db: AsyncSession, tenant_id: str, agent_key: str, trigger: str, context: dict, reasoning: str, calls: list,
               action: str, outcome: str) -> DecisionLog:
    log = DecisionLog(tenant_id=tenant_id, agent_key=agent_key, trigger_event_type=trigger, trigger_context=context,
                      reasoning=reasoning[:8000], tools_called=calls[:60], action_taken=action[:256], outcome=outcome)
    db.add(log)
    row = (await db.execute(select(Agent).where(Agent.key == agent_key, Agent.tenant_id == tenant_id))).scalar_one_or_none()
    if row:
        row.decisions_made += 1
    await db.commit()
    return log


async def run_task(db: AsyncSession, tenant_id: str, task_id: str, client: AnthropicAgentClient | None = None) -> dict:
    """Claims one task as its owning agent, does it, and reports back."""
    reader = SeoApi(tenant_id, AUTOPILOT)
    task = await reader.get(f"/tasks/{task_id}")
    owner = task["owning_agent"]
    status = await agent_status(db, tenant_id, owner)
    if status != AgentStatus.ACTIVE:
        return {"task_id": task_id, "type": task["type"], "outcome": "skipped", "detail": f"{owner} is {status.value}"}
    handler = HANDLERS.get(task["type"])
    api = SeoApi(tenant_id, owner)
    try:
        await api.post(f"/tasks/{task_id}/claim")
    except SeoApiError as exc:
        return {"task_id": task_id, "type": task["type"], "outcome": "skipped", "detail": exc.detail}
    ctx = Ctx(api=api, task=task, site=await api.get(f"/sites/{task['site_id']}"), client=client or AnthropicAgentClient())
    outcome, action = "success", f"{task['type']}_done"
    try:
        if handler is None:
            raise SeoApiError(422, f"no agent handles '{task['type']}'", "invalid")
        result = await handler(ctx)
        await api.post(f"/tasks/{task_id}/complete", {"result": result})
        detail = result.get("summary", "done")
    except SeoApiError as exc:
        blocked = exc.needs_integration
        outcome, action, detail = ("deferred", f"{task['type']}_blocked", exc.detail) if blocked else ("failed", f"{task['type']}_failed", exc.detail)
        try:
            await api.post(f"/tasks/{task_id}/fail", {"error": exc.detail, "blocked": blocked})
        except SeoApiError:
            logger.warning("could not record failure of task %s", task_id)
    except Exception as exc:  # never leave a task claimed after a crash
        logger.exception("SEO task %s crashed", task_id)
        outcome, action, detail = "failed", f"{task['type']}_failed", f"{type(exc).__name__}: {exc}"
        try:
            await api.post(f"/tasks/{task_id}/fail", {"error": detail[:500]})
        except SeoApiError:
            pass
    mode = "Claude" if ctx.live else "[deterministic fallback — no live Claude call]"
    reasoning = f"{task['title']}: {detail}. Work done with {mode}." + (f" Notes: {'; '.join(ctx.notes)}" if ctx.notes else "")
    await _log(db, tenant_id, owner, f"seo.task.{task['type']}", {"task_id": task_id, "site_id": task["site_id"], "params": task.get("params")},
               reasoning, api.calls, action, outcome)
    return {"task_id": task_id, "type": task["type"], "agent": owner, "outcome": outcome, "detail": detail}


async def run_cycle(db: AsyncSession, tenant_id: str, site_id: str, client: AnthropicAgentClient | None = None) -> dict:
    """One autopilot cycle: replan, read the plan, run what it allows."""
    status = await agent_status(db, tenant_id, AUTOPILOT)
    if status != AgentStatus.ACTIVE:
        await _log(db, tenant_id, AUTOPILOT, "seo.autopilot_tick", {"site_id": site_id}, f"Autopilot agent is {status.value}; no cycle ran.",
                   [], "skipped_agent_not_active", "deferred")
        return {"summary": f"autopilot agent is {status.value}", "ran": [], "held": []}
    api = SeoApi(tenant_id, AUTOPILOT)
    queued = (await api.post(f"/sites/{site_id}/replan"))["queued"]
    plan = await api.get(f"/sites/{site_id}/autopilot/plan")
    ran = []
    for entry in plan["eligible"]:
        ran.append(await run_task(db, tenant_id, entry["task_id"], client))
    outcomes = {o: sum(1 for r in ran if r["outcome"] == o) for o in ("success", "failed", "deferred", "skipped")}
    summary = (f"mode {plan['mode']}: {len(queued)} task(s) queued, {len(ran)} run "
               f"({outcomes['success']} done, {outcomes['failed']} failed, {outcomes['deferred']} blocked, {outcomes['skipped']} skipped), "
               f"{len(plan['held'])} held")
    held_reasons = sorted({h["reason"] for h in plan["held"]})[:6]
    await _log(db, tenant_id, AUTOPILOT, "seo.autopilot_tick", {"site_id": site_id},
               f"Cycle — {summary}." + (f" Held because: {'; '.join(held_reasons)}." if held_reasons else ""),
               api.calls + [{"tool": f"run {r['type']}", "agent": r.get("agent"), "outcome": r["outcome"]} for r in ran],
               "cycle_run", "success" if not outcomes["failed"] else "failed")
    return {"summary": summary, "queued": queued, "ran": ran, "held": plan["held"]}


async def intake(db: AsyncSession, tenant_id: str, request_id: str) -> dict:
    status = await agent_status(db, tenant_id, INTAKE)
    if status != AgentStatus.ACTIVE:
        return {"status": "received", "detail": f"intake agent is {status.value}; the request waits"}
    api = SeoApi(tenant_id, INTAKE)
    try:
        request = await api.post(f"/requests/{request_id}/convert")
    except SeoApiError as exc:
        return {"status": "error", "detail": exc.detail}
    await _log(db, tenant_id, INTAKE, "seo.email_received", {"request_id": request_id, "sender": request["sender"]},
               f"Request from {request['sender']} ('{request['subject'][:80]}'): {request['status'].replace('_', ' ')}, "
               f"{len(request['task_ids'])} task(s)" + (" — instruction-like text found, so the tasks wait for a person" if request["injection_suspected"] else ""),
               api.calls, f"request_{request['status']}", "success" if request["status"] == "converted" else "deferred")
    return {"status": request["status"], "task_ids": request["task_ids"]}


# ── event handlers (supervisor routing) ───────────────────────────

async def handle_autopilot_tick(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> Any:
    return await run_cycle(db, tenant_id, payload["site_id"])


async def handle_email_received(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> Any:
    return await intake(db, tenant_id, payload["request_id"])


async def handle_task_event(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> Any:
    return await run_task(db, tenant_id, payload["task_id"])


async def handle_site_registered(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> Any:
    """A new site gets a first strategy straight away (analysis only)."""
    api = SeoApi(tenant_id, "seo_strategy_agent")
    try:
        result = await api.post(f"/sites/{payload['site_id']}/strategy/generate")
        detail, outcome = f"first strategy v{result['strategy']['version']} with {len(result['strategy']['initiatives'])} initiatives", "success"
    except SeoApiError as exc:
        detail, outcome = exc.detail, "failed"
    return await _log(db, tenant_id, "seo_strategy_agent", event_type, payload, f"New site: {detail}.", api.calls, "strategy_generated", outcome)


async def handle_integration_tested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> Any:
    """A source that just started working is synced at once."""
    if payload.get("kind") not in ("search_console", "analytics", "backlink_index", "business_profile"):
        return None
    api = SeoApi(tenant_id, "seo_integration_agent")
    try:
        results = await api.post(f"/sites/{payload['site_id']}/integrations/sync")
        detail, outcome = "; ".join(f"{k}: {v}" for k, v in results.items()), "success"
    except SeoApiError as exc:
        detail, outcome = exc.detail, "failed"
    return await _log(db, tenant_id, "seo_integration_agent", event_type, payload, f"{payload['kind']} connected — synced: {detail}.",
                      api.calls, "integration_synced", outcome)


async def handle_audit_completed(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> Any:
    """Queues the fix work an audit found (the plan decides when it runs)."""
    api = SeoApi(tenant_id, "technical_seo_agent")
    fixable = await api.get(f"/sites/{payload['site_id']}/technical/fixable")
    if not fixable:
        return None
    await api.post(f"/sites/{payload['site_id']}/tasks", {"type": "technical_fix", "title": "Work through the open technical issues",
                                                          "params": {"scope": "open_issues"}, "impact": 80, "effort": 2, "confidence": 0.8})
    return await _log(db, tenant_id, "technical_seo_agent", event_type, payload, f"Audit found {len(fixable)} fixable issue(s); fix task queued.",
                      api.calls, "fix_task_queued", "success")


def event_routes() -> dict[str, list[tuple[str, Callable]]]:
    routes: dict[str, list[tuple[str, Callable]]] = {
        "seo.autopilot_tick": [(AUTOPILOT, handle_autopilot_tick)],
        "seo.email_received": [(INTAKE, handle_email_received)],
        "seo.site_registered": [("seo_strategy_agent", handle_site_registered)],
        "seo.integration_tested": [("seo_integration_agent", handle_integration_tested)],
        "seo.audit_completed": [("technical_seo_agent", handle_audit_completed)],
    }
    for task_type, agent_key in OWNER.items():
        routes[f"seo.task.{task_type}"] = [(agent_key, handle_task_event)]
    return routes


async def roster(db: AsyncSession, tenant_id: str) -> list[dict]:
    rows = {a.key: a for a in (await db.execute(select(Agent).where(Agent.tenant_id == tenant_id, Agent.key.in_(AGENT_KEYS)))).scalars()}
    return [{**a, "status": (rows[a["key"]].status if a["key"] in rows else AgentStatus.ACTIVE).value,
             "decisions": rows[a["key"]].decisions_made if a["key"] in rows else 0} for a in AGENTS]
