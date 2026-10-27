"""The growth dashboard and recurring progress reports."""
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import geo, links, local
from src.seo.common import activity, aware, days_ago, emit, get_owned, get_site, invalid, row
from src.seo.models import (
    SeoActivity,
    SeoContent,
    SeoKeyword,
    SeoLocation,
    SeoOrganicDaily,
    SeoPseoPage,
    SeoRankSnapshot,
    SeoReport,
    SeoSite,
    SeoTask,
    SeoTechnicalAudit,
    SeoTechnicalIssue,
    SeoTrafficDaily,
)
from src.services import delivery

WINDOW = 28


def _growth(cur: float, prev: float) -> float | None:
    return round((cur - prev) / prev * 100, 1) if prev else None


async def dashboard(db: AsyncSession, tenant_id: str, site_id: str, window: int = WINDOW) -> dict:
    site = await get_site(db, tenant_id, site_id)
    sid, today = site.id, date.today()
    start, prev_start = today - timedelta(days=window), today - timedelta(days=2 * window)

    days = list((await db.execute(select(SeoOrganicDaily).where(SeoOrganicDaily.site_id == sid, SeoOrganicDaily.day >= prev_start)
                                  .order_by(SeoOrganicDaily.day))).scalars())
    cur = [d for d in days if d.day >= start]
    prev = [d for d in days if d.day < start]
    clicks, impressions = sum(d.clicks for d in cur), sum(d.impressions for d in cur)
    positions = [d.position for d in cur if d.position]
    organic = {"has_data": bool(cur), "clicks": clicks, "impressions": impressions,
               "ctr": round(clicks / impressions, 4) if impressions else None,
               "avg_position": round(sum(positions) / len(positions), 2) if positions else None,
               "clicks_growth_pct": _growth(clicks, sum(d.clicks for d in prev)), "previous_clicks": sum(d.clicks for d in prev),
               "series": [{"day": d.day.isoformat(), "clicks": d.clicks, "impressions": d.impressions} for d in cur],
               "missing": None if cur else "connect Search Console"}

    tracked = {k.term for k in (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == sid, SeoKeyword.owner == site.domain))).scalars()}
    snaps = (await db.execute(select(SeoRankSnapshot).where(SeoRankSnapshot.site_id == sid, SeoRankSnapshot.on >= start)
                              .order_by(SeoRankSnapshot.on))).scalars()
    first, last = {}, {}
    for s in snaps:
        if s.keyword in tracked and s.position:
            first.setdefault(s.keyword, s.position)
            last[s.keyword] = s.position
    dist = Counter("1-3" if p <= 3 else "4-10" if p <= 10 else "11-20" if p <= 20 else "21+" for p in last.values())
    moves = [{"keyword": k, "from": round(first[k]), "to": round(p), "change": round(first[k] - p)} for k, p in last.items() if round(first[k]) != round(p)]
    rankings = {"has_data": bool(last), "source": "rank snapshots", "tracked": len(last), "top_10": sum(1 for p in last.values() if p <= 10),
                "distribution": {b: dist.get(b, 0) for b in ("1-3", "4-10", "11-20", "21+")},
                "gainers": sorted((m for m in moves if m["change"] > 0), key=lambda m: -m["change"])[:5],
                "decliners": sorted((m for m in moves if m["change"] < 0), key=lambda m: m["change"])[:5],
                "missing": None if last else "connect Search Console or keyword data"}

    content_items = list((await db.execute(select(SeoContent).where(SeoContent.site_id == sid, SeoContent.published_at.is_not(None)))).scalars())
    pseo_pages = list((await db.execute(select(SeoPseoPage).where(SeoPseoPage.site_id == sid, SeoPseoPage.published_at.is_not(None)))).scalars())
    published = [aware(c.published_at) for c in content_items] + [aware(p.published_at) for p in pseo_pages]
    weekly = []
    for w in range(7, -1, -1):
        end = today - timedelta(days=7 * w)
        weekly.append({"week_ending": end.isoformat(), "published": sum(1 for t in published if end - timedelta(days=7) < t.date() <= end)})
    content = {"published_total": len(published), "articles_total": len(content_items), "programmatic_total": len(pseo_pages),
               "published_period": sum(1 for t in published if t.date() >= start),
               "published_previous": sum(1 for t in published if prev_start <= t.date() < start), "weekly": weekly}

    authority = await links.authority(db, tenant_id, site_id)
    authority = {"has_data": authority["latest"] is not None, **authority, "missing": None if authority["latest"] else "connect the backlink index"}
    vis = await geo.visibility(db, tenant_id, site_id)
    vis = {"has_data": bool(vis["overall"]["checks"]), **vis, "missing": None if vis["overall"]["checks"] else "connect AI answer engines"}

    audit = (await db.execute(select(SeoTechnicalAudit).where(SeoTechnicalAudit.site_id == sid).order_by(SeoTechnicalAudit.created_at.desc()))).scalars().first()
    open_issues = dict((await db.execute(select(SeoTechnicalIssue.severity, func.count()).where(
        SeoTechnicalIssue.site_id == sid, SeoTechnicalIssue.status.in_(("open", "awaiting_approval", "planned", "failed")))
        .group_by(SeoTechnicalIssue.severity))).all())
    technical = {"has_data": audit is not None, "health_score": audit.health_score if audit else None,
                 "last_audit_at": aware(audit.created_at).isoformat() if audit else None, "open_issues": open_issues,
                 "missing": None if audit else "connect the site crawler"}

    locations = list((await db.execute(select(SeoLocation).where(SeoLocation.site_id == sid))).scalars())
    ranks = await local.rank(db, tenant_id, site_id)
    local_out = {"has_data": bool(locations), "locations": len(locations),
                 "avg_profile_score": round(sum(loc.gbp_score for loc in locations) / len(locations)) if locations else None,
                 "pack_rate": ranks["pack_rate"], "missing": None if locations else "add your locations"}

    traffic_days = list((await db.execute(select(SeoTrafficDaily).where(SeoTrafficDaily.site_id == sid, SeoTrafficDaily.day >= prev_start))).scalars())
    t_cur = [d for d in traffic_days if d.day >= start]
    t_prev = [d for d in traffic_days if d.day < start]
    traffic = {"has_data": bool(t_cur), "sessions": sum(d.sessions for d in t_cur), "organic_sessions": sum(d.organic_sessions for d in t_cur),
               "conversions": sum(d.conversions for d in t_cur),
               "organic_growth_pct": _growth(sum(d.organic_sessions for d in t_cur), sum(d.organic_sessions for d in t_prev)),
               "missing": None if t_cur else "connect web analytics"}

    by_status = dict((await db.execute(select(SeoTask.status, func.count()).where(
        SeoTask.site_id == sid, SeoTask.status.in_(("ready", "awaiting_approval", "in_progress", "backlog", "blocked")))
        .group_by(SeoTask.status))).all())
    done = (await db.execute(select(func.count()).select_from(SeoTask).where(SeoTask.site_id == sid, SeoTask.status == "done",
                                                                            SeoTask.completed_at >= days_ago(window)))).scalar() or 0
    return {"site": site.domain, "window_days": window, "organic": organic, "rankings": rankings, "content": content, "authority": authority,
            "ai_visibility": vis, "technical": technical, "local": local_out, "traffic": traffic,
            "tasks": {"by_status": by_status, "done_period": done}}


PERIODS = {"daily": 1, "weekly": 7, "monthly": 30}


async def generate(db: AsyncSession, site: SeoSite, period: str, actor: str) -> SeoReport:
    if period not in PERIODS:
        raise invalid("period must be daily, weekly or monthly")
    days = PERIODS[period]
    end, start = date.today(), date.today() - timedelta(days=days)
    dash = await dashboard(db, site.tenant_id, str(site.id), window=max(days, 7))
    counts = dict(Counter(k for k in (await db.execute(select(SeoActivity.kind).where(
        SeoActivity.site_id == site.id, SeoActivity.created_at >= days_ago(days)))).scalars()))
    held = (await db.execute(select(func.count()).select_from(SeoContent).where(SeoContent.site_id == site.id,
                                                                               SeoContent.status == "held_for_review"))).scalar() or 0
    waiting = dash["tasks"]["by_status"].get("awaiting_approval", 0)
    attention = []
    if dash["technical"]["open_issues"].get("critical"):
        attention.append(f"{dash['technical']['open_issues']['critical']} critical technical issue(s) are open")
    if held:
        attention.append(f"{held} content piece(s) are held for a person's review")
    if waiting:
        attention.append(f"{waiting} task(s) are waiting for approval")
    if dash["tasks"]["by_status"].get("blocked"):
        attention.append(f"{dash['tasks']['by_status']['blocked']} task(s) are blocked by a missing integration")
    o = dash["organic"]
    lines = [f"{period.title()} progress report for {site.domain} ({start} to {end})."]
    if o["has_data"]:
        growth = f" ({'+' if (o['clicks_growth_pct'] or 0) >= 0 else ''}{o['clicks_growth_pct']}% vs the previous period)" if o["clicks_growth_pct"] is not None else ""
        lines.append(f"Organic search: {o['clicks']} clicks{growth}.")
    if dash["rankings"]["has_data"]:
        lines.append(f"Rankings: {dash['rankings']['top_10']} tracked keyword(s) in the top 10.")
    lines.append(f"Content: {dash['content']['published_period']} piece(s) published this period, {dash['content']['published_total']} in total.")
    lines.append(f"Work done: {counts.get('content', 0)} content update(s), {counts.get('technical_fix', 0)} technical action(s), "
                 f"{counts.get('backlink', 0)} link update(s), {counts.get('ai_citation', 0)} AI-answer observation(s), "
                 f"{counts.get('local', 0)} local update(s).")
    if dash["ai_visibility"]["has_data"]:
        lines.append(f"AI-answer visibility: {dash['ai_visibility']['overall']['score']}/100 across {dash['ai_visibility']['overall']['checks']} checks.")
    if attention:
        lines.append("Needs attention: " + "; ".join(attention) + ".")
    report = SeoReport(tenant_id=site.tenant_id, site_id=site.id, period=period, period_start=start, period_end=end,
                       summary={"dashboard": dash, "activity_counts": counts, "needs_attention": attention}, narrative="\n".join(lines),
                       created_by=actor)
    db.add(report)
    await db.flush()
    await activity(db, site, "report", f"{period} progress report generated", actor, "report", report.id)
    await db.commit()
    await emit("seo.report_generated", site, {"report_id": report.id, "period": period})
    return report


async def send(db: AsyncSession, site: SeoSite, report: SeoReport) -> int:
    """Emails the report to the site's recipients."""
    sent = 0
    for to in site.report_recipients or []:
        result = await delivery.deliver(site.tenant_id, "email", to, report.narrative,
                                        f"{site.name}: {report.period} SEO report ({report.period_end})", "seo_report")
        sent += result.get("status") == "sent"
    return sent


async def list_reports(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(r, exclude=("summary",)) for r in (await db.execute(select(SeoReport).where(SeoReport.site_id == site.id)
                                                                    .order_by(SeoReport.created_at.desc()).limit(50))).scalars()]


async def get_report(db: AsyncSession, tenant_id: str, report_id: str) -> dict:
    return row(await get_owned(db, SeoReport, tenant_id, report_id, "report"))


async def set_narrative(db: AsyncSession, tenant_id: str, report_id: str, narrative: str) -> dict:
    report = await get_owned(db, SeoReport, tenant_id, report_id, "report")
    if not (narrative or "").strip():
        raise invalid("narrative is required")
    report.narrative = narrative.strip()[:8000]
    await db.commit()
    return row(report)
