"""Link building that search engines reward: relevant prospects only,
link schemes blocked outright, outreach that passes the guardrail and
the suppression list, and authority tracked over time."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import (
    SeoError,
    activity,
    conflict,
    connected,
    days_ago,
    get_owned,
    get_site,
    hard_gate,
    invalid,
    is_agent,
    row,
)
from src.seo.constants import (
    ALLOWED_LINK_TYPES,
    AUTHORITY_NOTE,
    BLOCKED_LINK_TYPES,
    DEFAULT_CAPS,
    MIN_LINK_RELEVANCE,
    PROSPECT_TRANSITIONS,
    SPAM_SCORE_BLOCK,
)
from src.seo.models import SeoAuthoritySnapshot, SeoBacklink, SeoCompetitor, SeoLinkProspect, SeoOutreach, SeoSite
from src.services import delivery, suppression_engine


def gate() -> dict:
    return {"blocked_link_types": sorted(BLOCKED_LINK_TYPES), "allowed_link_types": sorted(ALLOWED_LINK_TYPES),
            "spam_score_block": SPAM_SCORE_BLOCK, "min_relevance": MIN_LINK_RELEVANCE}


def _relevance(site: SeoSite, topics: str) -> float:
    profile = site.business_profile or {}
    mine = rules.stems(" ".join([*(profile.get("topics") or []), *(profile.get("products") or [])]))
    return round(100 * min(1.0, 2 * rules.jaccard(mine, rules.stems(topics)))) if topics else 0


async def upsert_prospect(db: AsyncSession, site: SeoSite, body: dict, actor: str) -> SeoLinkProspect:
    domain = rules.normalize_domain(body.get("domain") or "")
    if not rules.DOMAIN_RE.match(domain):
        raise invalid(f"'{body.get('domain')}' is not a valid domain")
    if domain == site.domain:
        raise invalid("a site cannot link to itself")
    link_type = body.get("link_type") or "editorial"
    if not rules.valid_link_type(link_type):
        raise invalid(f"unknown link_type '{link_type}'")
    relevance = body.get("relevance")
    relevance = float(relevance) if relevance is not None else _relevance(site, body.get("topics") or "")
    authority, spam = float(body.get("authority") or 0), float(body.get("spam_score") or 0)
    blocked = rules.link_block_reason(link_type, spam)
    prospect = (await db.execute(select(SeoLinkProspect).where(SeoLinkProspect.site_id == site.id, SeoLinkProspect.domain == domain))).scalar_one_or_none()
    if prospect is None:
        prospect = SeoLinkProspect(tenant_id=site.tenant_id, site_id=site.id, domain=domain, status="prospect")
        db.add(prospect)
    prospect.url = body.get("url") or prospect.url
    prospect.contact_email = (body.get("contact_email") or "").strip().lower() or prospect.contact_email
    prospect.link_type, prospect.authority, prospect.spam_score, prospect.relevance = link_type, authority, spam, relevance
    prospect.notes = str(body.get("notes") or prospect.notes or "")
    prospect.score = rules.prospect_score(relevance, authority, spam)
    if blocked:
        prospect.status, prospect.block_reason = "blocked", blocked
    elif prospect.status == "blocked":
        prospect.status, prospect.block_reason = "prospect", None
    await activity(db, site, "backlink", f"Link prospect {'blocked' if blocked else 'added'}: {domain}" +
                   (f" ({blocked})" if blocked else f" (score {prospect.score})"), actor, "prospect", domain)
    return prospect


async def add_prospect(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoLinkProspect:
    site = await get_site(db, tenant_id, site_id)
    prospect = await upsert_prospect(db, site, body, actor)
    await db.commit()
    return prospect


async def set_status(db: AsyncSession, tenant_id: str, prospect_id: str, status: str, actor: str) -> SeoLinkProspect:
    prospect = await get_owned(db, SeoLinkProspect, tenant_id, prospect_id, "prospect")
    if prospect.status == "blocked":
        raise hard_gate(f"this prospect is blocked by the link-scheme gate ({prospect.block_reason}) and cannot be moved")
    if status not in PROSPECT_TRANSITIONS.get(prospect.status, []):
        raise conflict(f"cannot move a prospect from {prospect.status} to {status}")
    if status == "approved_for_outreach":
        if prospect.relevance < MIN_LINK_RELEVANCE:
            raise conflict(f"relevance {prospect.relevance:g} is below the {MIN_LINK_RELEVANCE} needed: irrelevant links do not help and can look manipulative")
        if prospect.link_type == "sponsored" and is_agent(actor):
            raise hard_gate("sponsored placements are only ever initiated by a person (and must carry rel=sponsored)")
    prospect.status = status
    site = await get_site(db, tenant_id, prospect.site_id)
    await activity(db, site, "backlink", f"Prospect {prospect.domain} → {status}", actor, "prospect", prospect.id)
    await db.commit()
    return prospect


async def outreach(db: AsyncSession, tenant_id: str, prospect_id: str, body: dict, actor: str) -> SeoOutreach:
    """Checks an outreach email and, if it passes, sends it."""
    prospect = await get_owned(db, SeoLinkProspect, tenant_id, prospect_id, "prospect")
    site = await get_site(db, tenant_id, prospect.site_id)
    if prospect.status == "blocked":
        raise hard_gate(f"blocked by the link-scheme gate: {prospect.block_reason}")
    if prospect.status not in ("approved_for_outreach", "contacted"):
        raise conflict(f"prospect is {prospect.status}; approve it for outreach first")
    if not prospect.contact_email:
        raise invalid("the prospect has no contact email")
    subject, text = str(body.get("subject") or "").strip(), str(body.get("body") or "").strip()
    if not subject or not text:
        raise invalid("subject and body are required")
    if is_agent(actor):
        cap = (site.caps or DEFAULT_CAPS).get("max_outreach_per_day", DEFAULT_CAPS["max_outreach_per_day"])
        sent_today = (await db.execute(select(func.count()).select_from(SeoOutreach).where(
            SeoOutreach.site_id == site.id, SeoOutreach.created_at >= days_ago(1), SeoOutreach.status.in_(("queued", "sent"))))).scalar() or 0
        if sent_today >= cap:
            raise conflict(f"the daily outreach cap has been reached ({sent_today}/{cap})")
        recent = (await db.execute(select(SeoOutreach).where(SeoOutreach.prospect_id == prospect.id, SeoOutreach.status == "sent",
                                                             SeoOutreach.created_at >= days_ago(7)))).scalars().first()
        if recent:
            raise conflict("this prospect was already emailed in the last 7 days; a follow-up waits a week")
    findings = rules.outreach_findings(subject, text)
    suppressed = await suppression_engine.is_suppressed(db, tenant_id, "email", prospect.contact_email)
    status = "blocked_suppressed" if suppressed else ("blocked_guardrail" if findings else "queued")
    record = SeoOutreach(tenant_id=tenant_id, site_id=site.id, prospect_id=prospect.id, to_contact=prospect.contact_email,
                         subject=subject[:300], body=text, status=status,
                         findings=["recipient is on the suppression list"] if suppressed else findings, created_by=actor)
    db.add(record)
    if status == "queued":
        result = await delivery.deliver(tenant_id, "email", prospect.contact_email, text, subject, "seo_outreach")
        if result.get("status") == "sent":
            record.status, record.delivery_mode = "sent", result.get("delivery_mode")
            prospect.status = "contacted"
        else:
            record.status = "failed"
            record.findings = [result.get("error_message") or result.get("status") or "delivery failed"]
    await activity(db, site, "backlink", f"Outreach to {prospect.domain}: {record.status.replace('_', ' ')}", actor, "outreach", prospect.id)
    await db.commit()
    return record


async def discover(db: AsyncSession, site: SeoSite, actor: str, target_domains: list[str] | None = None) -> dict:
    """Prospects from the sites linking to competitors but not to us, plus
    any named targets (sources AI engines cite, for example)."""
    integ = await connected(db, site, "backlink_index")
    added, blocked = 0, 0
    if integ:
        ours = {d["domain"] for d in await adapters.referring_domains(site, integ, site.domain)}
        competitors = [c.domain for c in (await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id))).scalars()]
        candidates: dict[str, dict] = {}
        for comp in competitors[:3]:
            for d in await adapters.referring_domains(site, integ, comp):
                if d["domain"] not in ours and d["domain"] != site.domain:
                    candidates.setdefault(d["domain"], {**d, "topics": comp})
        for d in list(candidates.values())[:40]:
            prospect = await upsert_prospect(db, site, {"domain": d["domain"], "authority": d["authority"], "spam_score": d["spam_score"],
                                                        "relevance": max(MIN_LINK_RELEVANCE, _relevance(site, d["domain"].replace(".", " "))),
                                                        "notes": f"links to {d['topics']}"}, actor)
            added += prospect.status != "blocked"
            blocked += prospect.status == "blocked"
    for target in target_domains or []:
        prospect = await upsert_prospect(db, site, {"domain": target, "link_type": "editorial", "relevance": 60,
                                                    "notes": "cited by AI answer engines"}, actor)
        added += prospect.status != "blocked"
    if not integ and not target_domains:
        raise SeoError(409, "finding link prospects needs the backlink index integration connected", "provider_not_configured",
                       kind="backlink_index")
    await db.commit()
    return {"added": added, "blocked": blocked}


async def list_prospects(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(p) for p in (await db.execute(select(SeoLinkProspect).where(SeoLinkProspect.site_id == site.id)
                                              .order_by(SeoLinkProspect.score.desc()))).scalars()]


async def list_outreach(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(o) for o in (await db.execute(select(SeoOutreach).where(SeoOutreach.site_id == site.id)
                                              .order_by(SeoOutreach.created_at.desc()).limit(200))).scalars()]


async def list_backlinks(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(b) for b in (await db.execute(select(SeoBacklink).where(SeoBacklink.site_id == site.id)
                                              .order_by(SeoBacklink.acquired_at.desc()).limit(500))).scalars()]


async def authority(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    links = list((await db.execute(select(SeoBacklink).where(SeoBacklink.site_id == site.id))).scalars())
    series = list((await db.execute(select(SeoAuthoritySnapshot).where(SeoAuthoritySnapshot.site_id == site.id)
                                    .order_by(SeoAuthoritySnapshot.on))).scalars())
    latest = series[-1] if series else None
    month_ago = next((s for s in reversed(series) if latest and s.on <= latest.on - timedelta(days=28)), series[0] if series else None)
    growth = None
    if latest and month_ago and month_ago.referring_domains and latest.referring_domains is not None and month_ago is not latest:
        growth = round((latest.referring_domains - month_ago.referring_domains) / month_ago.referring_domains * 100, 1)
    return {
        "metric_note": AUTHORITY_NOTE, "backlinks_tracked": len(links), "active": sum(1 for b in links if b.status == "active"),
        "lost": sum(1 for b in links if b.status == "lost"), "dofollow": sum(1 for b in links if b.status == "active" and b.rel == "follow"),
        "latest": {"on": latest.on.isoformat(), "authority_score": latest.authority_score, "referring_domains": latest.referring_domains,
                   "backlinks": latest.backlinks, "source": latest.source} if latest else None,
        "delta": round(latest.authority_score - series[-2].authority_score, 1) if len(series) > 1 and latest.authority_score is not None
        and series[-2].authority_score is not None else None,
        "growth_pct_30d": growth,
        "series": [{"on": s.on.isoformat(), "authority_score": s.authority_score, "referring_domains": s.referring_domains} for s in series[-26:]],
    }
