"""Generative Engine Optimization: how the brand shows up in ChatGPT,
Perplexity, Claude and Google's AI answers, and the work that raises
the odds of being named and cited."""
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules, tasks
from src.seo.common import SeoError, activity, connected, days_ago, emit, get_owned, get_site, invalid, row
from src.seo.constants import GEO_DISCLAIMER, GEO_ENGINES
from src.seo.models import SeoCompetitor, SeoGeoCheck, SeoGeoPrompt, SeoSite

WINDOW_DAYS = 30


def default_prompts(site: SeoSite) -> list[tuple[str, str]]:
    profile = site.business_profile or {}
    seeds = [s.lower() for s in (profile.get("products") or profile.get("topics") or [site.name])][:3]
    out = []
    for seed in seeds:
        article = "an" if seed[:1] in "aeiou" else "a"
        out += [(f"What is the best {seed}?", seed), (f"How much does {article} {seed} cost?", seed),
                (f"Is {article} {seed} worth it?", seed), (f"What should I look for in {article} {seed}?", seed)]
        for market in (profile.get("markets") or [])[:2]:
            out.append((f"Best {seed} in {market.title()}", seed))
    return out


async def list_prompts(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    prompts = list((await db.execute(select(SeoGeoPrompt).where(SeoGeoPrompt.site_id == site.id).order_by(SeoGeoPrompt.created_at))).scalars())
    if not prompts:
        for text, cluster in default_prompts(site):
            prompts.append(SeoGeoPrompt(tenant_id=tenant_id, site_id=site.id, prompt=text, cluster=cluster))
        db.add_all(prompts)
        await db.commit()
    return [row(p) for p in prompts]


async def add_prompt(db: AsyncSession, tenant_id: str, site_id: str, body: dict) -> SeoGeoPrompt:
    site = await get_site(db, tenant_id, site_id)
    text = str(body.get("prompt") or "").strip()
    if not text:
        raise invalid("prompt is required")
    for p in (await db.execute(select(SeoGeoPrompt).where(SeoGeoPrompt.site_id == site.id))).scalars():
        if p.prompt.lower() == text.lower():
            return p
    prompt = SeoGeoPrompt(tenant_id=tenant_id, site_id=site.id, prompt=text[:500], cluster=str(body.get("cluster") or "")[:300])
    db.add(prompt)
    await db.commit()
    return prompt


async def set_prompt_active(db: AsyncSession, tenant_id: str, prompt_id: str, active: bool) -> SeoGeoPrompt:
    prompt = await get_owned(db, SeoGeoPrompt, tenant_id, prompt_id, "prompt")
    prompt.active = active
    await db.commit()
    return prompt


async def _competitors(db: AsyncSession, site: SeoSite) -> list[dict]:
    return [{"domain": c.domain, "name": ""} for c in (await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id))).scalars()]


async def _record(db: AsyncSession, site: SeoSite, prompt: SeoGeoPrompt, engine: str, answer: str, citations: list[str], actor: str) -> SeoGeoCheck:
    found = rules.analyze_answer(answer, citations, (site.business_profile or {}).get("brand_terms") or [], site.domain, await _competitors(db, site))
    check = SeoGeoCheck(tenant_id=site.tenant_id, site_id=site.id, prompt_id=prompt.id, engine=engine, answer_excerpt=answer[:1500],
                        cited_urls=citations[:30], cited_domains=found["cited_domains"][:30], brand_mentioned=found["brand_mentioned"],
                        mention_position=found["mention_position"], domain_cited=found["domain_cited"], sentiment=found["sentiment"],
                        competitors_mentioned=found["competitors_mentioned"])
    db.add(check)
    verdict = "brand not mentioned"
    if found["brand_mentioned"]:
        verdict = "brand mentioned" + (" and cited" if found["domain_cited"] else "")
    await activity(db, site, "ai_citation", f'{engine}: "{prompt.prompt[:60]}" — {verdict}', actor, "geo_check", prompt.id)
    return check


async def record_check(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoGeoCheck:
    """A check observed by hand (an answer copied from an AI assistant)."""
    site = await get_site(db, tenant_id, site_id)
    prompt = await get_owned(db, SeoGeoPrompt, tenant_id, body.get("prompt_id") or "", "prompt")
    if prompt.site_id != site.id:
        raise invalid("the prompt belongs to another site")
    if not str(body.get("answer_text") or "").strip():
        raise invalid("answer_text is required")
    if body.get("engine") not in GEO_ENGINES:
        raise invalid(f"engine must be one of {GEO_ENGINES}")
    check = await _record(db, site, prompt, body["engine"], body["answer_text"], [c for c in body.get("citations") or [] if c], actor)
    await db.commit()
    await emit("seo.geo_check_recorded", site, {"check_id": check.id})
    return check


async def run_checks(db: AsyncSession, site: SeoSite, actor: str, limit: int = 20) -> dict:
    integ = await connected(db, site, "geo_engine")
    if integ is None:
        raise SeoError(503, "no 'unconfigured' adapter is registered for the 'geo_engine' integration in this deployment",
                       "provider_not_configured", kind="geo_engine")
    await list_prompts(db, site.tenant_id, str(site.id))
    prompts = list((await db.execute(select(SeoGeoPrompt).where(SeoGeoPrompt.site_id == site.id, SeoGeoPrompt.active)
                                     .order_by(SeoGeoPrompt.created_at).limit(limit))).scalars())
    recorded, errors = 0, Counter()
    for prompt in prompts:
        for answer in await adapters.ask_engines(site, integ, prompt.prompt):
            if answer.get("error"):
                errors[f"{answer['engine']}: {answer['error'][:120]}"] += 1
                continue
            if rules.looks_like_injection(answer["answer"]):
                errors[f"{answer['engine']}: answer looked like instructions and was ignored"] += 1
                continue
            await _record(db, site, prompt, answer["engine"], answer["answer"], answer["citations"], actor)
            recorded += 1
        await db.commit()
    if not recorded and errors:
        raise SeoError(502, "no AI engine answered: " + "; ".join(errors), "provider_error")
    return {"prompts": len(prompts), "checks": recorded, "errors": dict(errors)}


async def _recent(db: AsyncSession, site: SeoSite) -> list[SeoGeoCheck]:
    return list((await db.execute(select(SeoGeoCheck).where(SeoGeoCheck.site_id == site.id,
                                                            SeoGeoCheck.checked_at >= days_ago(WINDOW_DAYS)))).scalars())


async def list_checks(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(c) for c in (await db.execute(select(SeoGeoCheck).where(SeoGeoCheck.site_id == site.id)
                                              .order_by(SeoGeoCheck.checked_at.desc()).limit(200))).scalars()]


async def visibility(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    checks = await _recent(db, site)
    previous = list((await db.execute(select(SeoGeoCheck).where(SeoGeoCheck.site_id == site.id,
                                                                SeoGeoCheck.checked_at < days_ago(WINDOW_DAYS),
                                                                SeoGeoCheck.checked_at >= days_ago(2 * WINDOW_DAYS)))).scalars())
    overall = rules.score_checks(checks)
    before = rules.score_checks(previous)
    competitor_mentions = Counter(d for c in checks for d in c.competitors_mentioned or [])
    brand = sum(1 for c in checks if c.brand_mentioned)
    total_mentions = brand + sum(competitor_mentions.values())
    return {
        "window_days": WINDOW_DAYS, "overall": overall,
        "by_engine": {e: rules.score_checks([c for c in checks if c.engine == e]) for e in sorted({c.engine for c in checks})},
        "share_of_voice": round(brand / total_mentions, 3) if total_mentions else None,
        "competitor_mentions": dict(competitor_mentions), "sentiment": dict(Counter(c.sentiment for c in checks if c.brand_mentioned)),
        "trend_delta": round(overall["score"] - before["score"], 1) if overall["score"] is not None and before["score"] is not None else None,
        "disclaimer": GEO_DISCLAIMER,
    }


async def gaps(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    """Answers where competitors are named and the brand is not, and the
    sources AI engines cite most."""
    site = await get_site(db, tenant_id, site_id)
    checks = await _recent(db, site)
    prompts = {p.id: p for p in (await db.execute(select(SeoGeoPrompt).where(SeoGeoPrompt.site_id == site.id))).scalars()}
    out = [{"prompt_id": str(c.prompt_id), "prompt": prompts[c.prompt_id].prompt if c.prompt_id in prompts else "", "engine": c.engine,
            "competitors_named": c.competitors_mentioned, "cited_domains": c.cited_domains}
           for c in checks if not c.brand_mentioned]
    cited = Counter(d for c in checks for d in set(c.cited_domains or []) if d != site.domain and not d.endswith("." + site.domain))
    return {"gaps": out[:50], "source_targets": [{"domain": d, "times_cited": n} for d, n in cited.most_common(10)], "disclaimer": GEO_DISCLAIMER}


async def optimize(db: AsyncSession, site: SeoSite, actor: str) -> dict:
    """Turns visibility gaps into work: a direct answer page per missed
    question, structured data, and presence on the sources AI cites."""
    found = await gaps(db, site.tenant_id, str(site.id))
    queued = []
    questions = list(dict.fromkeys(g["prompt"] for g in found["gaps"] if g["prompt"]))[:6]
    for q in questions:
        task, created = await tasks.create_task(db, site, {
            "type": "content_create", "title": f"Answer directly: {q}", "source": "agent",
            "params": {"keyword": q.rstrip("?").lower(), "kind": "faq", "reason": "geo_gap"}, "impact": 60, "effort": 2, "confidence": 0.6}, actor, commit=False)
        if created:
            queued.append(task.title)
    if questions:
        task, created = await tasks.create_task(db, site, {
            "type": "schema_implementation", "title": "Add FAQPage and Organization structured data to the pages that answer these questions",
            "source": "agent", "params": {"schema_types": ["FAQPage", "Organization"], "reason": "geo_gap"}, "impact": 50, "effort": 1,
            "confidence": 0.7}, actor, commit=False)
        if created:
            queued.append(task.title)
    targets = [t["domain"] for t in found["source_targets"][:5]]
    if targets:
        task, created = await tasks.create_task(db, site, {
            "type": "link_prospect", "title": "Get present on the sources AI engines already cite", "source": "agent",
            "params": {"target_domains": targets, "reason": "geo_source_gap"}, "impact": 55, "effort": 3, "confidence": 0.5}, actor, commit=False)
        if created:
            queued.append(task.title)
    await activity(db, site, "ai_citation", f"AI-visibility plan: {len(queued)} task(s) queued from {len(found['gaps'])} gap(s)", actor)
    await db.commit()
    return {"tasks_queued": queued, "gaps": len(found["gaps"])}
