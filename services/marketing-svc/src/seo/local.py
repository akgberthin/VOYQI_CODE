"""Local SEO: Business Profile completeness, NAP consistency across
directories, and local-pack rankings per location.

Agents may only change what the checklist marks agent_can_do
(description, services, posts, Q&A answers, review replies) — never
name, address, phone, categories, photos or reviews themselves."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import SeoError, activity, connected, get_owned, get_site, hard_gate, invalid, is_agent, now, row
from src.seo.constants import GBP_CHECKLIST
from src.seo.models import SeoLocalRank, SeoLocation, SeoSite

AGENT_ITEMS = {i["item"] for i in GBP_CHECKLIST if i["agent_can_do"]}
LOCATION_FIELDS = ("name", "address", "phone", "website", "categories", "services", "hours", "gbp_location_id", "citations", "local_keywords")


def checklist() -> list[dict]:
    return GBP_CHECKLIST


def location_out(loc: SeoLocation) -> dict:
    return row(loc)


async def create_location(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoLocation:
    site = await get_site(db, tenant_id, site_id)
    if not str(body.get("name") or "").strip():
        raise invalid("name is required")
    loc = SeoLocation(tenant_id=tenant_id, site_id=site.id, name=body["name"].strip()[:256], profile=body.get("profile") or {},
                      **{f: body[f] for f in LOCATION_FIELDS if f in body and f != "name"})
    loc.gbp_score, loc.gbp_missing = rules.gbp_score(loc)
    db.add(loc)
    await activity(db, site, "local", f"Location added: {loc.name}", actor, "location", loc.name)
    await db.commit()
    return loc


async def update_location(db: AsyncSession, tenant_id: str, location_id: str, body: dict, actor: str) -> SeoLocation:
    if is_agent(actor):
        raise hard_gate("a location's name, address, phone, hours and categories are changed by a person; agents use the profile changes")
    loc = await get_owned(db, SeoLocation, tenant_id, location_id, "location")
    for f in LOCATION_FIELDS:
        if f in body:
            setattr(loc, f, body[f])
    if "profile" in body:
        loc.profile = {**(loc.profile or {}), **(body["profile"] or {})}
    loc.gbp_score, loc.gbp_missing = rules.gbp_score(loc)
    await db.commit()
    return loc


def local_keywords(site: SeoSite, loc: SeoLocation) -> list[str]:
    if loc.local_keywords:
        return list(loc.local_keywords)
    city = ((loc.address or {}).get("city") or "").lower()
    products = [p.lower() for p in ((site.business_profile or {}).get("products") or [])][:2] or [site.name.lower()]
    out = [f"{p} {city}".strip() for p in products if city]
    return out + [f"{products[0]} near me"]


async def audit(db: AsyncSession, tenant_id: str, location_id: str, actor: str) -> SeoLocation:
    """Re-scores the profile, checks local-pack rankings when search-results
    data is connected, and records what is missing."""
    loc = await get_owned(db, SeoLocation, tenant_id, location_id, "location")
    site = await get_site(db, tenant_id, loc.site_id)
    gbp = await connected(db, site, "business_profile")
    if gbp and loc.gbp_location_id:
        try:
            data = await adapters.gbp_pull(site, gbp, loc.gbp_location_id)
            if data:
                loc.profile = {**(loc.profile or {}), "description": data.get("description"), "special_hours": data.get("special_hours")}
                loc.categories = data.get("categories") or loc.categories
        except SeoError:
            pass
    loc.gbp_score, loc.gbp_missing = rules.gbp_score(loc)
    loc.last_audit_at = now()
    ranked = []
    serp = await connected(db, site, "serp_data")
    if serp:
        for kw in local_keywords(site, loc)[:4]:
            try:
                position, in_pack = await adapters.local_pack_position(site, serp, kw, loc)
            except SeoError:
                continue
            db.add(SeoLocalRank(tenant_id=tenant_id, site_id=site.id, location_id=loc.id, keyword=kw, position=position, in_pack=in_pack))
            ranked.append(f"{kw}: {position or 'not ranked'}")
    await activity(db, site, "local", f"{loc.name}: profile {loc.gbp_score}/100" + (f"; {', '.join(ranked)}" if ranked else ""),
                   actor, "location", loc.id)
    await db.commit()
    return loc


async def audit_site(db: AsyncSession, site: SeoSite, actor: str) -> dict:
    out = {}
    for loc in (await db.execute(select(SeoLocation).where(SeoLocation.site_id == site.id))).scalars().all():
        loc = await audit(db, site.tenant_id, str(loc.id), actor)
        out[loc.name] = loc.gbp_score
    return out


async def apply_change(db: AsyncSession, tenant_id: str, location_id: str, body: dict, actor: str) -> dict:
    """One profile change: description, services, posts, qa or
    review_response. Pushed to the live profile when Business Profile is
    connected; otherwise kept as pending for a person to copy over."""
    loc = await get_owned(db, SeoLocation, tenant_id, location_id, "location")
    site = await get_site(db, tenant_id, loc.site_id)
    item, value = body.get("item"), body.get("value")
    if item not in {i["item"] for i in GBP_CHECKLIST}:
        raise invalid(f"unknown profile item '{item}'")
    if is_agent(actor) and item not in AGENT_ITEMS:
        raise hard_gate(f"'{item}' is changed by a person on the profile itself; agents may change {sorted(AGENT_ITEMS)}")
    if value in (None, "", []):
        raise invalid("value is required")
    text = " ".join(value) if isinstance(value, list) else str(value)
    findings = rules.guardrail(text)
    if findings:
        raise invalid("the change trips the guardrail: " + "; ".join(findings))
    if item == "description" and len(text) < 250:
        raise invalid(f"a description needs at least 250 characters ({len(text)} given)")
    pushed = None
    gbp = await connected(db, site, "business_profile")
    if gbp and loc.gbp_location_id and item in ("description", "posts"):
        pushed = await adapters.gbp_push(site, gbp, loc.gbp_location_id, {"item": item, "value": text})
    profile = dict(loc.profile or {})
    if item == "description":
        profile["description"] = text
    elif item == "services":
        loc.services = list(value) if isinstance(value, list) else [s.strip() for s in text.split(",") if s.strip()]
    elif item == "posts" and pushed:
        profile["posts_last_7d"] = (profile.get("posts_last_7d") or 0) + 1
    if not pushed:
        pending = list(profile.get("pending") or [])
        pending.append({"item": item, "value": value, "by": actor, "at": now().isoformat()})
        profile["pending"] = pending[-20:]
    loc.profile = profile
    loc.gbp_score, loc.gbp_missing = rules.gbp_score(loc)
    await activity(db, site, "local", f"{loc.name}: {item} {'updated on the live profile' if pushed else 'ready for a person to copy to the profile'}",
                   actor, "location", loc.id)
    await db.commit()
    return {"location": location_out(loc), "pushed": bool(pushed), "detail": pushed or "kept as a pending change"}


async def nap(db: AsyncSession, tenant_id: str, location_id: str) -> dict:
    loc = await get_owned(db, SeoLocation, tenant_id, location_id, "location")
    details = [rules.nap_compare(loc, c) for c in loc.citations or []]
    consistent = sum(1 for d in details if d["consistent"])
    return {"location_id": str(loc.id), "name": loc.name, "citations": len(details), "consistent": consistent,
            "consistency_pct": round(consistent / len(details) * 100) if details else None, "details": details}


async def list_locations(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [location_out(loc) for loc in (await db.execute(select(SeoLocation).where(SeoLocation.site_id == site.id)
                                                           .order_by(SeoLocation.created_at))).scalars()]


async def keywords(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    out = []
    for loc in (await db.execute(select(SeoLocation).where(SeoLocation.site_id == site.id))).scalars():
        out += [{"keyword": kw, "location": loc.name, "location_id": str(loc.id)} for kw in local_keywords(site, loc)]
    return out


async def rank(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    locations, tracked, in_pack = [], 0, 0
    for loc in (await db.execute(select(SeoLocation).where(SeoLocation.site_id == site.id))).scalars():
        latest: dict[str, SeoLocalRank] = {}
        for r in (await db.execute(select(SeoLocalRank).where(SeoLocalRank.location_id == loc.id).order_by(SeoLocalRank.checked_at))).scalars():
            latest[r.keyword] = r
        kws = [{"keyword": r.keyword, "position": r.position, "in_pack": r.in_pack} for r in latest.values()]
        tracked += len(kws)
        in_pack += sum(1 for k in kws if k["in_pack"])
        locations.append({"location_id": str(loc.id), "name": loc.name, "tracked": len(kws), "in_pack": sum(1 for k in kws if k["in_pack"]),
                          "keywords": kws})
    return {"tracked": tracked, "in_pack": in_pack, "pack_rate": round(in_pack / tracked, 3) if tracked else None, "locations": locations}
