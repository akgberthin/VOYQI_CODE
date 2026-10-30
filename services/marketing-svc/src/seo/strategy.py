"""The SEO strategy: initiatives built from the site's real data, ordered
by expected impact per effort, each with the tasks that carry it out."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import geo, rules, tasks
from src.seo.common import activity, days_ago, emit, get_owned, get_site, invalid, row
from src.seo.constants import DEFAULT_CAPS, IMPACT_DISCLAIMER
from src.seo.models import (
    SeoAuthoritySnapshot,
    SeoContent,
    SeoCroFinding,
    SeoIntegration,
    SeoKeyword,
    SeoLocation,
    SeoPseoPage,
    SeoPseoRow,
    SeoSite,
    SeoStrategy,
    SeoTechnicalIssue,
)


def _initiative(key: str, area: str, title: str, rationale: str, impact: float, effort: float, confidence: float,
                evidence: dict, task_specs: list[dict], blocked_by: list[str] | None = None) -> dict:
    return {"key": key, "area": area, "title": title, "rationale": rationale, "impact": round(rules.clamp(impact)), "effort": effort,
            "confidence": confidence, "priority": rules.task_priority(impact, effort, confidence), "evidence": evidence,
            "tasks": task_specs, "blocked_by": blocked_by or []}


async def build(db: AsyncSession, site: SeoSite) -> tuple[list[dict], dict]:
    sid = site.id
    integrations = {i.kind: i.status for i in (await db.execute(select(SeoIntegration).where(SeoIntegration.site_id == sid))).scalars()
                    if i.status != "not_connected"}
    connected = {k for k, s in integrations.items() if s == "connected"}
    keywords = list((await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == sid))).scalars())
    own = [k for k in keywords if k.owner == site.domain]
    initiatives: list[dict] = []

    striking = sorted((k for k in own if k.position and 4 <= k.position <= 20 and k.url), key=lambda k: -(k.volume or 0))[:5]
    if striking:
        initiatives.append(_initiative(
            "striking_distance", "content", f"Push {len(striking)} page(s) from positions 4-20 toward the top",
            "Pages already close to page one respond fastest to a refresh.", 75, 2, 0.75,
            {"pages": [{"term": k.term, "position": k.position, "url": k.url} for k in striking]},
            [{"type": "content_refresh", "title": f"Refresh the page ranking #{k.position} for '{k.term}'",
              "params": {"keyword": k.term, "url": k.url}} for k in striking]))

    issues = dict((await db.execute(select(SeoTechnicalIssue.severity, func.count()).where(
        SeoTechnicalIssue.site_id == sid, SeoTechnicalIssue.status.in_(("open", "awaiting_approval", "planned", "failed")))
        .group_by(SeoTechnicalIssue.severity))).all())
    if issues.get("critical") or issues.get("warning"):
        c, w = issues.get("critical", 0), issues.get("warning", 0)
        initiatives.append(_initiative(
            "technical_fixes", "technical", f"Resolve {c} critical and {w} warning technical issues",
            "Technical problems cap how well everything else can rank.", min(100, 40 + c * 15 + w * 3), 3, 0.8,
            {"critical": c, "warning": w}, [{"type": "technical_fix", "title": "Work through the open technical issues", "params": {"scope": "open_issues"}}],
            [] if "cms" in connected else ["cms"]))

    weak = [loc for loc in (await db.execute(select(SeoLocation).where(SeoLocation.site_id == sid))).scalars() if loc.gbp_score < 80]
    if weak:
        initiatives.append(_initiative(
            "local_profiles", "local", f"Improve {len(weak)} Google Business Profile(s) scoring below 80",
            "Profile completeness drives local-pack visibility.", 50 + 4 * len(weak), 2, 0.75,
            {"locations": [{"name": loc.name, "score": loc.gbp_score, "missing": loc.gbp_missing[:4]} for loc in weak]},
            [{"type": "gbp_optimize", "title": f"Optimize the profile for {loc.name}", "params": {"location_id": str(loc.id)}} for loc in weak]))

    findings = (await db.execute(select(func.count()).select_from(SeoCroFinding).where(SeoCroFinding.site_id == sid,
                                                                                      SeoCroFinding.status == "open"))).scalar() or 0
    if findings:
        initiatives.append(_initiative(
            "conversion", "conversion", f"Act on {findings} open conversion finding(s)", "Traffic that does not convert is wasted effort.",
            min(80, 45 + 2 * findings), 2, 0.6, {"open_findings": findings},
            [{"type": "cro_experiment", "title": "Test the highest-impact conversion change", "params": {}}]))

    pending_rows = (await db.execute(select(func.count()).select_from(SeoPseoRow).where(SeoPseoRow.site_id == sid, SeoPseoRow.generated.is_(False)))).scalar() or 0
    approved_pages = (await db.execute(select(func.count()).select_from(SeoPseoPage).where(SeoPseoPage.site_id == sid,
                                                                                          SeoPseoPage.status == "approved"))).scalar() or 0
    if pending_rows or approved_pages:
        initiatives.append(_initiative(
            "programmatic_generate", "advanced", f"Generate and publish {pending_rows + approved_pages} programmatic page(s)",
            "Template data is waiting. Each page is generated, measured against its siblings and published only if it is genuinely "
            "different and deep enough.", 50, 2, 0.6, {"pending": pending_rows, "approved": approved_pages},
            [{"type": "pseo_batch", "title": f"Generate and publish {pending_rows + approved_pages} programmatic page(s)", "params": {}}],
            [] if "cms" in connected else ["cms"]))

    covered = {c.target_keyword for c in (await db.execute(select(SeoContent).where(SeoContent.site_id == sid))).scalars()}
    gaps = sorted((k for k in keywords if k.is_gap and k.term not in covered), key=lambda k: -k.opportunity)[:5]
    if gaps:
        initiatives.append(_initiative(
            "close_keyword_gaps", "content", f"Cover {len(gaps)} high-opportunity topic(s) competitors rank for and you do not",
            "These terms have competitor coverage, meaningful demand and buying intent, with no ranking page of yours in the top 20.",
            min(85, 40 + sum(k.opportunity for k in gaps) / len(gaps) / 3), 3, 0.7,
            {"top_gaps": [{"term": k.term, "volume": k.volume, "purchase_intent": k.purchase_intent, "opportunity": k.opportunity,
                           "competitor": k.owner} for k in gaps]},
            [{"type": "content_create", "title": f"Create content for '{k.term}'",
              "params": {"keyword": k.term, "cluster": k.cluster, "intent": k.intent}} for k in gaps]))

    vis = await geo.visibility(db, site.tenant_id, str(sid))
    if vis["overall"]["checks"] and (vis["overall"]["score"] or 0) < 60:
        initiatives.append(_initiative(
            "ai_visibility", "geo", f"Raise AI-answer visibility from {vis['overall']['score']}/100",
            "AI assistants increasingly answer before anyone clicks; being named and cited there is a new front door.",
            65, 2, 0.55, {"overall": vis["overall"], "competitor_mentions": vis["competitor_mentions"]},
            [{"type": "geo_optimize", "title": "Turn AI-answer gaps into content and citations", "params": {}}]))

    latest = (await db.execute(select(SeoAuthoritySnapshot).where(SeoAuthoritySnapshot.site_id == sid)
                               .order_by(SeoAuthoritySnapshot.on.desc()))).scalars().first()
    if "backlink_index" in connected and latest and (latest.referring_domains or 0) < 200:
        initiatives.append(_initiative(
            "authority", "links", f"Earn links from relevant sites ({latest.referring_domains} referring domains today)",
            "Relevant editorial links are still among the strongest ranking signals.", 55, 4, 0.5,
            {"authority_score": latest.authority_score, "referring_domains": latest.referring_domains},
            [{"type": "link_prospect", "title": "Find relevant link prospects", "params": {}}]))

    published_30d = (await db.execute(select(func.count()).select_from(SeoContent).where(
        SeoContent.site_id == sid, SeoContent.published_at >= days_ago(30)))).scalar() or 0
    target = 2 * (site.caps or DEFAULT_CAPS).get("max_publish_per_week", 4)
    if published_30d < target:
        initiatives.append(_initiative(
            "publishing_cadence", "content", f"Keep a steady publishing cadence ({published_30d} of ~{target} pieces in the last 30 days)",
            "A recurring schedule compounds; gaps in it do not.", 60, 3, 0.7, {"published_30d": published_30d, "target": target}, []))

    missing = [k for k in ("search_console", "cms", "crawler", "keyword_data") if k not in connected]
    if missing:
        initiatives.append(_initiative(
            "connect_data", "setup", f"Connect {', '.join(m.replace('_', ' ') for m in missing)}",
            "Without these the agents cannot measure, research or publish — work that needs them is held.", 70, 1, 0.9,
            {"not_connected": missing}, []))

    initiatives.sort(key=lambda i: -i["priority"])
    snapshot = {"keywords": len(keywords), "gaps": sum(1 for k in keywords if k.is_gap), "open_issues": issues,
                "published_30d": published_30d, "integrations": integrations, "disclaimer": IMPACT_DISCLAIMER}
    return initiatives, snapshot


async def generate(db: AsyncSession, tenant_id: str, site_id: str, actor: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    initiatives, snapshot = await build(db, site)
    version = ((await db.execute(select(func.max(SeoStrategy.version)).where(SeoStrategy.site_id == site.id))).scalar() or 0) + 1
    profile = site.business_profile or {}
    top = "; ".join(i["title"] for i in initiatives[:3]) or "nothing urgent: keep measuring"
    strategy = SeoStrategy(tenant_id=tenant_id, site_id=site.id, version=version, initiatives=initiatives, inputs_snapshot=snapshot,
                           summary=f"Strategy v{version} for {site.domain}" + (f" ({profile['positioning']})" if profile.get("positioning") else "")
                           + f": {len(initiatives)} initiative(s), highest priority first. Top: {top}.", generated_by=actor)
    db.add(strategy)
    await db.flush()
    created = []
    for initiative in initiatives:
        if initiative["blocked_by"]:
            continue
        for spec in initiative["tasks"]:
            task, is_new = await tasks.create_task(db, site, {**spec, "source": "strategy", "strategy_id": strategy.id,
                                                              "impact": initiative["impact"], "effort": initiative["effort"],
                                                              "confidence": initiative["confidence"]}, actor, commit=False)
            if is_new:
                created.append(str(task.id))
    await activity(db, site, "strategy", f"Strategy v{version} generated: {len(initiatives)} initiatives, {len(created)} new task(s)",
                   actor, "strategy", strategy.id)
    await db.commit()
    await emit("seo.strategy_generated", site, {"strategy_id": strategy.id, "version": version})
    return {"strategy": row(strategy), "tasks_created": created, "disclaimer": IMPACT_DISCLAIMER}


async def current(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    latest = (await db.execute(select(SeoStrategy).where(SeoStrategy.site_id == site.id).order_by(SeoStrategy.version.desc()))).scalars().first()
    return {"strategy": row(latest) if latest else None, "disclaimer": IMPACT_DISCLAIMER}


async def history(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(s, exclude=("initiatives",)) for s in (await db.execute(select(SeoStrategy).where(SeoStrategy.site_id == site.id)
                                                                        .order_by(SeoStrategy.version.desc()).limit(20))).scalars()]


async def set_narrative(db: AsyncSession, tenant_id: str, strategy_id: str, narrative: str) -> dict:
    """The strategy agent's plain-language explanation of the plan."""
    strategy = await get_owned(db, SeoStrategy, tenant_id, strategy_id, "strategy")
    text = (narrative or "").strip()
    if not text:
        raise invalid("narrative is required")
    strategy.narrative = text[:8000]
    await db.commit()
    return row(strategy)
