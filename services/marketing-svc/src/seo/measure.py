"""Pulling measurement data in from connected sources: Search Console,
web analytics, the backlink index and Business Profiles."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import SeoError, activity, connected, now
from src.seo.models import (
    SeoAuthoritySnapshot,
    SeoBacklink,
    SeoKeyword,
    SeoLinkProspect,
    SeoLocation,
    SeoOrganicDaily,
    SeoPageStat,
    SeoRankSnapshot,
    SeoSite,
    SeoTrafficDaily,
)


async def _upsert(db: AsyncSession, model, keys: dict, values: dict):
    q = select(model)
    for k, v in keys.items():
        q = q.where(getattr(model, k) == v)
    found = (await db.execute(q)).scalar_one_or_none()
    if found is None:
        found = model(**keys, **values)
        db.add(found)
    else:
        for k, v in values.items():
            setattr(found, k, v)
    return found


async def sync_search_console(db: AsyncSession, site: SeoSite, integ) -> str:
    data = await adapters.search_console_sync(site, integ)
    base = {"tenant_id": site.tenant_id, "site_id": site.id}
    for d in data["daily"]:
        await _upsert(db, SeoOrganicDaily, {**base, "day": date.fromisoformat(d["day"])},
                      {"clicks": d["clicks"], "impressions": d["impressions"], "position": d["position"]})
    top_query: dict[str, tuple[int, str]] = {}
    for q in data["queries"]:
        if q["clicks"] >= top_query.get(q["url"], (-1, ""))[0]:
            top_query[q["url"]] = (q["clicks"], q["query"])
    for window, rows_ in (("current", data["pages_current"]), ("previous", data["pages_previous"])):
        for p in rows_:
            await _upsert(db, SeoPageStat, {**base, "url": p["url"], "window": window},
                          {"clicks": p["clicks"], "impressions": p["impressions"], "position": p["position"],
                           "top_query": top_query.get(p["url"], (0, None))[1], "synced_at": now()})
    as_of = date.fromisoformat(data["as_of"])
    best: dict[str, dict] = {}
    for q in data["queries"]:
        current = best.get(q["query"])
        if current is None or q["clicks"] > current["clicks"]:
            best[q["query"]] = q
    own = {k.term: k for k in (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.owner == site.domain))).scalars()}
    for term, q in best.items():
        await _upsert(db, SeoRankSnapshot, {**base, "keyword": term[:300], "on": as_of, "source": "search_console"},
                      {"position": q["position"], "url": q["url"]})
        if term in own:
            own[term].position, own[term].url = round(q["position"]), q["url"]
    return f"{len(data['daily'])} days, {len(data['pages_current'])} pages, {len(best)} queries"


async def sync_analytics(db: AsyncSession, site: SeoSite, integ) -> str:
    data = await adapters.analytics_sync(site, integ)
    for d in data["daily"]:
        await _upsert(db, SeoTrafficDaily, {"tenant_id": site.tenant_id, "site_id": site.id, "day": date.fromisoformat(d["day"])},
                      {"sessions": d["sessions"], "organic_sessions": d["organic_sessions"], "conversions": d["conversions"]})
    return f"{len(data['daily'])} days of traffic"


async def sync_backlinks(db: AsyncSession, site: SeoSite, integ, actor: str) -> str:
    summary = await adapters.backlink_summary(site, integ)
    base = {"tenant_id": site.tenant_id, "site_id": site.id}
    await _upsert(db, SeoAuthoritySnapshot, {**base, "on": date.today()},
                  {"authority_score": summary["authority_score"], "referring_domains": summary["referring_domains"],
                   "backlinks": summary["backlinks"], "source": summary["source"]})
    links = await adapters.backlinks(site, integ)
    seen = set()
    prospects = {p.domain: p for p in (await db.execute(select(SeoLinkProspect).where(SeoLinkProspect.site_id == site.id))).scalars()}
    for link in links:
        seen.add((link["source_url"], link["target_url"]))
        domain = rules.normalize_domain(link["source_domain"] or "")
        prospect = prospects.get(domain)
        found = await _upsert(db, SeoBacklink, {**base, "source_url": link["source_url"][:1024], "target_url": link["target_url"][:1024]},
                              {"source_domain": domain, "anchor": link["anchor"][:512], "rel": link["rel"], "status": "active", "lost_at": None})
        if found.authority_at_acquisition is None:
            found.authority_at_acquisition = link.get("authority")
        if prospect and prospect.status in ("contacted", "replied", "approved_for_outreach"):
            prospect.status = "acquired"
            found.prospect_id = prospect.id
            await activity(db, site, "backlink", f"Link acquired from {domain}", actor, "prospect", prospect.id)
    lost = 0
    if links:  # an empty answer is not evidence that every link is gone
        for link in (await db.execute(select(SeoBacklink).where(SeoBacklink.site_id == site.id, SeoBacklink.status == "active"))).scalars():
            if (link.source_url, link.target_url) not in seen:
                link.status, link.lost_at = "lost", now()
                lost += 1
    return f"authority {summary['authority_score']}, {len(links)} backlinks" + (f", {lost} lost" if lost else "")


async def sync_profiles(db: AsyncSession, site: SeoSite, integ) -> str:
    updated = 0
    for loc in (await db.execute(select(SeoLocation).where(SeoLocation.site_id == site.id))).scalars():
        if not loc.gbp_location_id:
            continue
        data = await adapters.gbp_pull(site, integ, loc.gbp_location_id)
        if not data:
            continue
        loc.name = data.get("name") or loc.name
        loc.phone = data.get("phone") or loc.phone
        loc.website = data.get("website") or loc.website
        loc.categories = data.get("categories") or loc.categories
        loc.profile = {**(loc.profile or {}), "description": data.get("description") or (loc.profile or {}).get("description"),
                       "special_hours": data.get("special_hours")}
        updated += 1
    return f"{updated} profile(s) refreshed"


async def sync_all(db: AsyncSession, site: SeoSite, actor: str) -> dict:
    """Syncs every connected measurement source. One failing source is
    recorded on its integration; the rest still sync."""
    results: dict[str, str] = {}
    steps = (("search_console", sync_search_console), ("analytics", sync_analytics),
             ("backlink_index", lambda d, s, i: sync_backlinks(d, s, i, actor)), ("business_profile", sync_profiles))
    for kind, step in steps:
        integ = await connected(db, site, kind)
        if integ is None:
            continue
        try:
            results[kind] = await step(db, site, integ)
            integ.last_sync_at, integ.last_error = now(), None
        except SeoError as exc:
            integ.last_error = exc.detail
            results[kind] = f"failed: {exc.detail}"
        await activity(db, site, "integration", f"Synced {kind.replace('_', ' ')}: {results[kind]}", actor, "integration", kind)
        await db.commit()
    return results
