"""Keyword research: what the site and its competitors rank for, where
the gaps are, what people ask — scored by buying intent and opportunity."""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import SeoError, activity, connected, emit, get_site, invalid, now, row
from src.seo.models import SeoCompetitor, SeoKeyword, SeoRankSnapshot, SeoSite

MARKET = "market"  # owner of keyword ideas nobody ranks for yet


def cluster_of(term: str, seeds: list[str]) -> str:
    matches = [s for s in seeds if s and s in term]
    return max(matches, key=len) if matches else term


def _seeds(site: SeoSite) -> list[str]:
    profile = site.business_profile or {}
    return sorted({s.lower() for s in [*(profile.get("products") or []), *(profile.get("topics") or [])] if s}, key=len, reverse=True)


async def _store(db: AsyncSession, site: SeoSite, rows_: list[dict], owner: str, source: str) -> int:
    seeds, brands = _seeds(site), (site.business_profile or {}).get("brand_terms") or []
    existing = {(k.term, k.owner): k for k in (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.owner == owner))).scalars()}
    stored = 0
    for r in rows_:
        term = (r.get("term") or "").strip().lower()[:300]
        if not term:
            continue
        intent, purchase = rules.classify_intent(term, brands)
        values = {
            "volume": r.get("volume"), "difficulty": r.get("difficulty"), "cpc_cents": r.get("cpc_cents"),
            "position": r.get("position"), "url": r.get("url"), "intent": intent, "purchase_intent": purchase,
            "cluster": cluster_of(term, seeds)[:300], "trend": r.get("trend") or "flat", "growth_pct": r.get("growth_pct"),
            "is_question": rules.is_question(term), "source": source, "updated_at": now(),
            "opportunity": rules.keyword_opportunity(r.get("volume"), r.get("difficulty"), purchase, r.get("position") if owner == site.domain else None),
        }
        kw = existing.get((term, owner))
        if kw is None:
            kw = SeoKeyword(tenant_id=site.tenant_id, site_id=site.id, term=term, owner=owner, **values)
            db.add(kw)
            existing[(term, owner)] = kw
        else:
            for k, v in values.items():
                setattr(kw, k, v)
        stored += 1
    return stored


async def mark_gaps(db: AsyncSession, site: SeoSite) -> int:
    """A gap: a competitor ranks top 20 for a term the site has no page
    in the top 20 for."""
    keywords = list((await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id))).scalars())
    covered_terms = {k.term for k in keywords if k.owner == site.domain and k.position and k.position <= 20}
    gaps = 0
    for k in keywords:
        gap = k.owner not in (site.domain, MARKET) and bool(k.position and k.position <= 20) and k.term not in covered_terms
        k.is_gap = gap
        gaps += gap
    return gaps


async def sync(db: AsyncSession, site: SeoSite, actor: str) -> dict:
    """Keyword research from whatever is connected: the keyword-data
    provider (site, competitors, ideas) and Search Console (real queries)."""
    kw_integ = await connected(db, site, "keyword_data")
    gsc = await connected(db, site, "search_console")
    if not kw_integ and not gsc:
        raise SeoError(409, "keyword research needs the keyword-data or Search Console integration connected", "provider_not_configured",
                       kind="keyword_data")
    counts: dict[str, int] = defaultdict(int)
    if kw_integ:
        source = f"{kw_integ.provider} keyword-data import"
        counts["site"] = await _store(db, site, await adapters.ranked_keywords(site, kw_integ, site.domain), site.domain, source)
        competitors = list((await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id))).scalars())
        if not competitors:
            for c in (await adapters.find_competitors(site, kw_integ))[:3]:
                comp = SeoCompetitor(tenant_id=site.tenant_id, site_id=site.id, domain=c["domain"], notes="found by keyword overlap")
                db.add(comp)
                competitors.append(comp)
                await activity(db, site, "research", f"Competitor found: {c['domain']}", actor, "competitor", c["domain"])
        for comp in competitors[:5]:
            counts["competitors"] += await _store(db, site, await adapters.ranked_keywords(site, kw_integ, comp.domain), comp.domain, source)
        for seed in _seeds(site)[:3]:
            counts["ideas"] += await _store(db, site, await adapters.keyword_ideas(site, kw_integ, seed), MARKET, source)
    if gsc:
        latest = (await db.execute(select(SeoRankSnapshot).where(SeoRankSnapshot.site_id == site.id, SeoRankSnapshot.source == "search_console")
                                   .order_by(SeoRankSnapshot.on.desc()).limit(2000))).scalars()
        seen, gsc_rows = set(), []
        for snap in latest:
            if snap.keyword not in seen:
                seen.add(snap.keyword)
                gsc_rows.append({"term": snap.keyword, "position": round(snap.position) if snap.position else None, "url": snap.url})
        known = {k.term for k in (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.owner == site.domain))).scalars()}
        counts["search_console"] = await _store(db, site, [r for r in gsc_rows if r["term"] not in known], site.domain, "search console queries")
    await db.flush()
    counts["gaps"] = await mark_gaps(db, site)
    await activity(db, site, "keyword", "Keyword research refreshed: " + ", ".join(f"{v} {k}" for k, v in counts.items()), actor)
    await db.commit()
    await emit("seo.keywords_imported", site, dict(counts))
    return dict(counts)


async def import_keywords(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> dict:
    """For teams that export keywords from their own tool."""
    site = await get_site(db, tenant_id, site_id)
    items = body.get("keywords") or []
    if not isinstance(items, list) or not items:
        raise invalid("keywords must be a non-empty list")
    by_owner: dict[str, list] = defaultdict(list)
    for k in items:
        by_owner[rules.normalize_domain(k.get("owner") or site.domain) or site.domain].append(k)
    stored = 0
    for owner, rows_ in by_owner.items():
        stored += await _store(db, site, rows_, owner, str(body.get("source") or "manual import")[:128])
    await db.flush()
    gaps = await mark_gaps(db, site)
    await activity(db, site, "keyword", f"{stored} keyword(s) imported", actor)
    await db.commit()
    return {"imported": stored, "gaps": gaps}


async def list_keywords(db: AsyncSession, tenant_id: str, site_id: str, f: dict) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoKeyword).where(SeoKeyword.site_id == site.id)
    if f.get("intent"):
        q = q.where(SeoKeyword.intent == f["intent"])
    if f.get("min_purchase_intent"):
        q = q.where(SeoKeyword.purchase_intent >= int(f["min_purchase_intent"]))
    if f.get("gaps_only") in ("true", True):
        q = q.where(SeoKeyword.is_gap.is_(True))
    if f.get("questions_only") in ("true", True):
        q = q.where(SeoKeyword.is_question.is_(True))
    if f.get("cluster"):
        q = q.where(SeoKeyword.cluster == f["cluster"])
    if f.get("owner"):
        q = q.where(SeoKeyword.owner == f["owner"])
    q = q.order_by(SeoKeyword.opportunity.desc()).limit(min(int(f.get("limit") or 200), 1000))
    return [row(k, exclude=("tenant_id", "site_id")) for k in (await db.execute(q)).scalars()]


async def gaps(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    return await list_keywords(db, tenant_id, site_id, {"gaps_only": True, "limit": 100})


async def questions(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    merged: dict[str, dict] = {}
    for k in (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.is_question.is_(True)))).scalars():
        entry = merged.setdefault(k.term, {"question": k.term, "cluster": k.cluster, "volume": k.volume, "sources": [],
                                           "purchase_intent": k.purchase_intent})
        entry["volume"] = max(entry["volume"] or 0, k.volume or 0) or None
        source = "keyword_data" if k.owner != site.domain else "search_console"
        if source not in entry["sources"]:
            entry["sources"].append(source)
    return sorted(merged.values(), key=lambda q: -(q["volume"] or 0))[:200]
