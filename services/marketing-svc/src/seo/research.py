"""Market research: search results (Google and Bing), competitors,
Reddit, news and YouTube, digested into themes, questions and gaps.

Everything gathered is data. An item whose text reads like an
instruction to an AI is flagged and left out of what agents read."""
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import SeoError, activity, connected, emit, get_site, integration, invalid, now, row
from src.seo.constants import RESEARCH_SOURCES
from src.seo.models import SeoCompetitor, SeoKeyword, SeoResearchItem, SeoSite

STOP_THEMES = rules.STOPWORDS | {"best", "top", "guide", "new", "2024", "2025", "2026", "vs", "review", "reviews", "com", "www", "https"}


async def _store(db: AsyncSession, site: SeoSite, source: str, query: str, items: list[dict], engine: str | None = None) -> int:
    existing = {i.url: i for i in (await db.execute(select(SeoResearchItem).where(SeoResearchItem.site_id == site.id,
                                                                                  SeoResearchItem.source == source))).scalars()}
    stored = 0
    for it in items:
        url = (it.get("url") or "")[:1024]
        if not url:
            continue
        text = f"{it.get('title', '')}\n{it.get('summary', '')}"
        values = {"query": query[:300], "title": (it.get("title") or "")[:512], "summary": it.get("summary") or "",
                  "signals": it.get("signals") or {}, "engine": engine, "retrieved_at": now(),
                  "injection_suspected": rules.looks_like_injection(text)}
        found = existing.get(url)
        if found is None:
            found = SeoResearchItem(tenant_id=site.tenant_id, site_id=site.id, source=source, url=url, **values)
            db.add(found)
            existing[url] = found
        else:
            for k, v in values.items():
                setattr(found, k, v)
        stored += 1
    return stored


async def _queries(db: AsyncSession, site: SeoSite, limit: int = 5) -> list[str]:
    top = (await db.execute(select(SeoKeyword.term).where(SeoKeyword.site_id == site.id)
                            .order_by(SeoKeyword.opportunity.desc()).limit(limit))).scalars().all()
    profile = site.business_profile or {}
    seeds = [*(profile.get("products") or []), *(profile.get("topics") or [])]
    return list(dict.fromkeys([*top, *[s.lower() for s in seeds]]))[:limit] or [site.name.lower()]


async def run(db: AsyncSession, site: SeoSite, actor: str, sources: list[str] | None = None) -> dict:
    """Gathers research from every connected source (or those named)."""
    wanted = set(sources or [s for s, _l, _i in RESEARCH_SOURCES])
    counts: dict[str, int | str] = {}
    queries = await _queries(db, site)
    serp_integ = await connected(db, site, "serp_data")

    async def attempt(source: str, coro_fn) -> None:
        try:
            counts[source] = await coro_fn()
        except SeoError as exc:
            counts[source] = f"failed: {exc.detail}"

    if serp_integ and "serp" in wanted:
        async def serp_run() -> int:
            n = 0
            for q in queries:
                result = await adapters.serp(site, serp_integ, q)
                n += await _store(db, site, "serp", q, [{**o, "signals": {"position": o["position"], "serp_features": result["features"]}}
                                                       for o in result["organic"]], "google")
            return n
        await attempt("serp", serp_run)
    if serp_integ and "multi_engine" in wanted:
        async def multi_run() -> int:
            n = 0
            for q in queries[:2]:
                google = await adapters.serp(site, serp_integ, q)
                bing = await adapters.serp(site, serp_integ, q, engine="bing")
                google_urls = {o["url"] for o in google["organic"]}
                n += await _store(db, site, "multi_engine", q, [{**o, "signals": {"position": o["position"], "in_google": o["url"] in google_urls}}
                                                               for o in bing["organic"]], "bing")
            return n
        await attempt("multi_engine", multi_run)
    kw_integ = await connected(db, site, "keyword_data")
    if kw_integ and "competitor" in wanted:
        async def comp_run() -> int:
            found = await adapters.find_competitors(site, kw_integ)
            tracked = {c.domain for c in (await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id))).scalars()}
            items = [{"title": f"{c['domain']} shares {c.get('shared_keywords') or '?'} keywords", "url": f"https://{c['domain']}/",
                      "signals": {"shared_keywords": c.get("shared_keywords"), "avg_position": c.get("avg_position"),
                                  "tracked": c["domain"] in tracked}} for c in found]
            return await _store(db, site, "competitor", site.domain, items)
        await attempt("competitor", comp_run)
    for source, kind in (("reddit", "community_research"), ("news", "news_research"), ("youtube", "video_research")):
        integ = await connected(db, site, kind)
        if integ and source in wanted:
            async def src_run(source=source, integ=integ) -> int:
                n = 0
                for q in queries[:2]:
                    n += await _store(db, site, source, q, await adapters.research_items(site, integ, source, q))
                return n
            await attempt(source, src_run)
    if not counts:
        raise SeoError(409, "no research source is connected (search-results data, keyword data, Reddit, news or YouTube)",
                       "provider_not_configured", kind="serp_data")
    await activity(db, site, "research", "Research gathered: " + ", ".join(f"{k} {v}" for k, v in counts.items()), actor)
    await db.commit()
    await emit("seo.research_ingested", site, counts)
    return counts


async def list_items(db: AsyncSession, tenant_id: str, site_id: str, include_flagged: bool, source: str | None = None) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoResearchItem).where(SeoResearchItem.site_id == site.id)
    if not include_flagged:
        q = q.where(SeoResearchItem.injection_suspected.is_(False))
    if source:
        q = q.where(SeoResearchItem.source == source)
    return [row(i) for i in (await db.execute(q.order_by(SeoResearchItem.retrieved_at.desc()).limit(300))).scalars()]


async def sources(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    items = Counter(s for s in (await db.execute(select(SeoResearchItem.source).where(SeoResearchItem.site_id == site.id))).scalars())
    out = []
    for source, label, kind in RESEARCH_SOURCES:
        integ = await integration(db, site, kind)
        out.append({"source": source, "label": label, "integration": kind, "integration_status": integ.status, "items": items.get(source, 0)})
    await db.commit()
    return out


async def digest(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    all_items = list((await db.execute(select(SeoResearchItem).where(SeoResearchItem.site_id == site.id))).scalars())
    items = [i for i in all_items if not i.injection_suspected]
    themes: Counter = Counter()
    for i in items:
        for w in rules.stems(i.title):
            if w not in STOP_THEMES and not w.isdigit() and len(w) > 3:
                themes[w] += 1
    questions = Counter(i.title.strip() for i in items if i.title.strip().endswith("?") or rules.is_question(i.title))
    for k in (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.is_question.is_(True))
                               .order_by(SeoKeyword.volume.desc()).limit(10))).scalars():
        questions[k.term] += 1
    competitors = {c.domain for c in (await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id))).scalars()}
    coverage = Counter(rules.domain_of(i.url) for i in items if i.source == "serp" and rules.domain_of(i.url) in competitors)
    agreement = [i.url for i in items if i.source == "multi_engine" and (i.signals or {}).get("in_google")]
    intents = Counter(rules.classify_intent(q, (site.business_profile or {}).get("brand_terms"))[0]
                      for q in {i.query for i in items if i.source == "serp" and i.query})
    gaps = (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.is_gap.is_(True))
                             .order_by(SeoKeyword.opportunity.desc()).limit(6))).scalars()
    news = sorted((i for i in items if i.source == "news"), key=lambda i: i.retrieved_at, reverse=True)[:5]
    return {
        "site": site.domain, "items": len(items), "excluded_as_instruction_like": len(all_items) - len(items),
        "by_source": dict(Counter(i.source for i in items)),
        "themes": [{"theme": t, "mentions": n} for t, n in themes.most_common(8)],
        "questions": [{"question": q, "mentions": n} for q, n in questions.most_common(8)],
        "competitor_coverage": dict(coverage), "multi_engine_agreement": agreement[:10], "search_intent": dict(intents),
        "competitor_gaps": [{"term": g.term, "volume": g.volume, "opportunity": g.opportunity, "cluster": g.cluster} for g in gaps],
        "freshest_news": [{"title": i.title, "url": i.url} for i in news],
        "video_topics": [{"title": i.title, "url": i.url} for i in items if i.source == "youtube"][:5],
    }


async def search_intent(db: AsyncSession, tenant_id: str, site_id: str, query: str) -> dict:
    """Intent and the format that ranks for a query, from stored search
    results (fetched live once when a provider is connected)."""
    site = await get_site(db, tenant_id, site_id)
    q = (query or "").strip()
    if not q:
        raise invalid("query is required")

    async def stored() -> list[dict]:
        found = (await db.execute(select(SeoResearchItem).where(SeoResearchItem.site_id == site.id, SeoResearchItem.source == "serp",
                                                                SeoResearchItem.injection_suspected.is_(False)))).scalars()
        return [{"title": i.title, "signals": i.signals} for i in found if (i.query or "").strip().lower() == q.lower()]

    items = await stored()
    integ = await connected(db, site, "serp_data")
    if not items and integ:
        try:
            result = await adapters.serp(site, integ, q)
            await _store(db, site, "serp", q, [{**o, "signals": {"position": o["position"], "serp_features": result["features"]}}
                                              for o in result["organic"]], "google")
            await db.commit()
            items = await stored()
        except SeoError:
            items = []
    return rules.search_intent(q, (site.business_profile or {}).get("brand_terms") or [], items)
