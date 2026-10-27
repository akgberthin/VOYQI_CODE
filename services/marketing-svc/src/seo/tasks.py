"""The task queue the SEO agents work from, and the autopilot plan that
decides which ready task may run now.

A task runs unattended only when the site's autonomy policy allows its
type, a person has approved it if it needed approval, its cap has room
and there is a free concurrency slot. Agents claim a task before working
on it and the claim re-checks the plan, so nothing an agent does can
skip these rules."""
import json
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import rules
from src.seo.common import (
    activity,
    aware,
    conflict,
    days_ago,
    emit,
    get_owned,
    get_site,
    hard_gate,
    invalid,
    is_agent,
    now,
    row,
)
from src.seo.constants import CAP_FOR_TASK, DEFAULT_CAPS, OPEN_TASK_STATUSES, TASK_TYPES
from src.seo.models import (
    SeoContent,
    SeoGeoCheck,
    SeoGeoPrompt,
    SeoIntegration,
    SeoKeyword,
    SeoLinkProspect,
    SeoLocation,
    SeoOutreach,
    SeoPseoPage,
    SeoPseoRow,
    SeoReport,
    SeoResearchItem,
    SeoSite,
    SeoStrategy,
    SeoTask,
    SeoTechnicalAudit,
    SeoTechnicalIssue,
)

STALE_CLAIM = timedelta(hours=2)


def task_types() -> list[dict]:
    from src.seo.constants import ASSISTED_SAFE

    return [{"type": t, "owning_agent": a, "default_autonomy": d, "label": label, "assisted_safe": t in ASSISTED_SAFE}
            for t, (a, d, label) in TASK_TYPES.items()]


async def create_task(db: AsyncSession, site: SeoSite, body: dict, actor: str, *, commit: bool = True) -> tuple[SeoTask, bool]:
    """(task, created). An identical open task is returned instead of a
    duplicate."""
    task_type = body.get("type")
    if task_type not in TASK_TYPES:
        raise invalid(f"unknown task type '{task_type}'")
    title = str(body.get("title") or TASK_TYPES[task_type][2]).strip()[:300]
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise invalid("params must be an object")
    key = json.dumps(params, sort_keys=True, default=str)
    open_same = (await db.execute(select(SeoTask).where(SeoTask.site_id == site.id, SeoTask.type == task_type,
                                                        SeoTask.status.in_(OPEN_TASK_STATUSES)))).scalars()
    for existing in open_same:
        if json.dumps(existing.params or {}, sort_keys=True, default=str) == key:
            return existing, False
    impact = float(body.get("impact") if body.get("impact") is not None else 50)
    effort = float(body.get("effort") if body.get("effort") is not None else 3)
    confidence = float(body.get("confidence") if body.get("confidence") is not None else 0.6)
    injection = bool(body.get("injection"))
    allowed, reason = rules.autonomy(site, task_type, injection)
    needs_approval = not allowed and (injection or (site.autonomy_overrides or {}).get(task_type) == "approval"
                                      or TASK_TYPES[task_type][1] == "approval")
    task = SeoTask(
        tenant_id=site.tenant_id, site_id=site.id, type=task_type, title=title, description=str(body.get("description") or ""),
        params=params, impact=impact, effort=effort, confidence=confidence, priority=rules.task_priority(impact, effort, confidence),
        status="awaiting_approval" if needs_approval else "ready", source=body.get("source") or ("agent" if is_agent(actor) else "manual"),
        owning_agent=TASK_TYPES[task_type][0], requires_approval=needs_approval, approval_reason=reason if needs_approval else None,
        strategy_id=body.get("strategy_id"), created_by=actor,
    )
    db.add(task)
    await db.flush()
    await activity(db, site, "task", f"Task queued: {title[:120]}", actor, "task", task.id)
    if commit:
        await db.commit()
        await emit("seo.task_created", site, {"task_id": task.id, "type": task_type, "status": task.status})
    return task, True


async def list_tasks(db: AsyncSession, tenant_id: str, site_id: str, status: str | None, task_type: str | None) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoTask).where(SeoTask.site_id == site.id)
    if status:
        q = q.where(SeoTask.status == status)
    if task_type:
        q = q.where(SeoTask.type == task_type)
    return [row(t) for t in (await db.execute(q.order_by(SeoTask.priority.desc(), SeoTask.created_at))).scalars()]


async def approve(db: AsyncSession, tenant_id: str, task_id: str, actor: str) -> SeoTask:
    task = await get_owned(db, SeoTask, tenant_id, task_id, "task")
    if is_agent(actor):
        raise hard_gate("a task that waits for approval can only be approved by a person, never by an agent")
    # A ready task the autonomy policy holds (assisted mode, say) can be
    # approved too: a person deciding is exactly what the hold waits for.
    if task.status not in ("awaiting_approval", "ready") or task.approved_by:
        raise conflict(f"task is {task.status}" + (f" and already approved by {task.approved_by}" if task.approved_by else "")
                       + "; only a task waiting for a decision can be approved")
    task.status, task.approved_by = "ready", actor
    site = await get_site(db, tenant_id, task.site_id)
    await activity(db, site, "task", f"Task approved: {task.title[:120]}", actor, "task", task.id)
    await db.commit()
    return task


async def dismiss(db: AsyncSession, tenant_id: str, task_id: str, actor: str) -> SeoTask:
    task = await get_owned(db, SeoTask, tenant_id, task_id, "task")
    if task.status in ("done", "dismissed"):
        raise conflict(f"task is already {task.status}")
    task.status = "dismissed"
    site = await get_site(db, tenant_id, task.site_id)
    await activity(db, site, "task", f"Task dismissed: {task.title[:120]}", actor, "task", task.id)
    await db.commit()
    return task


# ── the plan ──────────────────────────────────────────────────────

async def cap_usage(db: AsyncSession, site: SeoSite) -> dict[str, int]:
    week, day = days_ago(7), days_ago(1)

    async def count(q) -> int:
        return int((await db.execute(q)).scalar() or 0)

    return {
        "max_publish_per_week": await count(select(func.count()).select_from(SeoContent).where(
            SeoContent.site_id == site.id, SeoContent.published_at >= week)),
        "max_pseo_pages_per_day": await count(select(func.count()).select_from(SeoPseoPage).where(
            SeoPseoPage.site_id == site.id, SeoPseoPage.published_at >= day)),
        "max_outreach_per_day": await count(select(func.count()).select_from(SeoOutreach).where(
            SeoOutreach.site_id == site.id, SeoOutreach.created_at >= day, SeoOutreach.status.in_(("queued", "sent")))),
        "max_auto_fixes_per_day": await count(select(func.count()).select_from(SeoTechnicalIssue).where(
            SeoTechnicalIssue.site_id == site.id, SeoTechnicalIssue.fixed_at >= day)),
    }


async def plan(db: AsyncSession, site: SeoSite) -> dict:
    caps = {**DEFAULT_CAPS, **(site.caps or {})}
    tasks = list((await db.execute(select(SeoTask).where(SeoTask.site_id == site.id, SeoTask.status.in_(("ready", "in_progress"))))).scalars())
    in_progress = sum(1 for t in tasks if t.status == "in_progress")
    slots = max(0, caps["max_concurrent_tasks"] - in_progress)
    used = await cap_usage(db, site)
    eligible, held = [], []
    for task in sorted((t for t in tasks if t.status == "ready"), key=lambda t: -t.priority):
        ok, reason = rules.autonomy(site, task.type)
        if task.requires_approval and not task.approved_by:
            ok, reason = False, task.approval_reason or "needs approval"
        elif task.approved_by and not ok and site.autopilot_mode != "off":
            ok, reason = True, f"approved by {task.approved_by}"
        cap = CAP_FOR_TASK.get(task.type)
        if ok and cap:
            if used[cap] >= caps.get(cap, 0):
                ok, reason = False, f"{cap} reached ({used[cap]}/{caps.get(cap, 0)})"
            else:
                used[cap] += 1
        if ok and len(eligible) >= slots:
            ok, reason = False, f"concurrency limit reached ({caps['max_concurrent_tasks']} tasks at once)"
        entry = {"task_id": str(task.id), "type": task.type, "title": task.title}
        if ok:
            eligible.append({**entry, "owning_agent": task.owning_agent, "priority": task.priority, "params": task.params})
        else:
            held.append({**entry, "reason": reason})
    return {"mode": site.autopilot_mode, "in_progress": in_progress, "slots_free": slots, "caps": caps, "eligible": eligible, "held": held}


# ── agent lifecycle ───────────────────────────────────────────────

async def claim(db: AsyncSession, tenant_id: str, task_id: str, actor: str) -> SeoTask:
    """Moves a ready task to in_progress, if the plan allows it right now."""
    task = await get_owned(db, SeoTask, tenant_id, task_id, "task")
    site = await get_site(db, tenant_id, task.site_id)
    if task.status != "ready":
        raise conflict(f"task is {task.status}; only a ready task can be claimed")
    current = await plan(db, site)
    held = next((h for h in current["held"] if h["task_id"] == str(task.id)), None)
    if held:
        raise conflict(f"not allowed to run now: {held['reason']}")
    result = await db.execute(update(SeoTask).where(SeoTask.id == task.id, SeoTask.status == "ready")
                              .values(status="in_progress", claimed_at=now()))
    if result.rowcount != 1:
        raise conflict("another worker claimed this task first")
    await db.commit()
    await db.refresh(task)
    return task


async def finish(db: AsyncSession, tenant_id: str, task_id: str, outcome: str, body: dict, actor: str) -> SeoTask:
    """outcome: done / failed / blocked."""
    task = await get_owned(db, SeoTask, tenant_id, task_id, "task")
    if task.status != "in_progress":
        raise conflict(f"task is {task.status}; only a task in progress can be finished")
    site = await get_site(db, tenant_id, task.site_id)
    task.status, task.completed_at = outcome, now()
    if outcome == "done":
        task.result, task.error = body.get("result") or {}, None
        summary = (body.get("result") or {}).get("summary")
        await activity(db, site, "task", f"Done: {task.title[:110]}" + (f" — {summary[:200]}" if summary else ""), actor, "task", task.id)
    else:
        task.error = str(body.get("error") or "no reason given")[:2000]
        await activity(db, site, "task", f"{'Blocked' if outcome == 'blocked' else 'Failed'}: {task.title[:110]} — {task.error[:200]}",
                       actor, "task", task.id)
    await db.commit()
    return task


async def unblock(db: AsyncSession, site: SeoSite) -> int:
    """After an integration starts working, blocked tasks get another go."""
    result = await db.execute(update(SeoTask).where(SeoTask.site_id == site.id, SeoTask.status == "blocked")
                              .values(status="ready", error=None, completed_at=None))
    return result.rowcount or 0


# ── housekeeping each cycle ───────────────────────────────────────

async def _latest(db: AsyncSession, column, *where) -> object:
    return (await db.execute(select(func.max(column)).where(*where))).scalar()


def _older(value, days: float) -> bool:
    return value is None or aware(value) < days_ago(days)


async def replan(db: AsyncSession, site: SeoSite, actor: str) -> list[str]:
    """Queues the recurring work a site needs, based on what has gone
    stale. Returns the types queued."""
    stale = await db.execute(update(SeoTask).where(SeoTask.site_id == site.id, SeoTask.status == "in_progress",
                                                   SeoTask.claimed_at < now() - STALE_CLAIM).values(status="ready", claimed_at=None))
    # Blocked work gets another go once a day (a key or a connection may
    # have been added since).
    await db.execute(update(SeoTask).where(SeoTask.site_id == site.id, SeoTask.status == "blocked",
                                           SeoTask.completed_at < now() - timedelta(hours=24))
                     .values(status="ready", error=None, completed_at=None))
    connected = {i.kind for i in (await db.execute(select(SeoIntegration).where(
        SeoIntegration.site_id == site.id, SeoIntegration.status == "connected"))).scalars()}
    wanted: list[dict] = []
    sid = site.id

    last_strategy = await _latest(db, SeoStrategy.created_at, SeoStrategy.site_id == sid)
    newer_data = await _latest(db, SeoKeyword.updated_at, SeoKeyword.site_id == sid)
    if _older(last_strategy, 7) or (newer_data and aware(newer_data) > aware(last_strategy)):
        wanted.append({"type": "strategy_refresh", "title": "Refresh the SEO strategy from current data", "impact": 60, "effort": 1, "confidence": 0.9})
    measuring = connected & {"search_console", "analytics", "backlink_index", "business_profile"}
    if measuring:
        last_sync = await _latest(db, SeoIntegration.last_sync_at, SeoIntegration.site_id == sid, SeoIntegration.kind.in_(measuring))
        if _older(last_sync, 0.8):
            wanted.append({"type": "integration_sync", "title": "Sync connected data sources", "impact": 55, "effort": 1, "confidence": 0.95})
    if "crawler" in connected and _older(await _latest(db, SeoTechnicalAudit.created_at, SeoTechnicalAudit.site_id == sid), 7):
        wanted.append({"type": "technical_audit", "title": "Weekly technical SEO audit", "impact": 70, "effort": 2, "confidence": 0.9})
    if connected & {"keyword_data", "search_console"} and _older(await _latest(db, SeoKeyword.updated_at, SeoKeyword.site_id == sid), 14):
        wanted.append({"type": "keyword_research", "title": "Refresh keyword data and opportunities", "impact": 65, "effort": 2, "confidence": 0.8})
    if connected & {"serp_data", "community_research", "news_research", "video_research"} and \
            _older(await _latest(db, SeoResearchItem.retrieved_at, SeoResearchItem.site_id == sid), 7):
        wanted.append({"type": "research_digest", "title": "Weekly market research digest", "impact": 45, "effort": 2, "confidence": 0.7})
    has_prompts = (await db.execute(select(func.count()).select_from(SeoGeoPrompt).where(SeoGeoPrompt.site_id == sid, SeoGeoPrompt.active))).scalar()
    if "geo_engine" in connected and has_prompts and \
            _older(await _latest(db, SeoGeoCheck.checked_at, SeoGeoCheck.site_id == sid), 7):
        wanted.append({"type": "geo_check", "title": "Check AI-answer visibility", "impact": 60, "effort": 1, "confidence": 0.85})
    has_locations = (await db.execute(select(func.count()).select_from(SeoLocation).where(SeoLocation.site_id == sid))).scalar()
    if has_locations and _older(await _latest(db, SeoLocation.last_audit_at, SeoLocation.site_id == sid), 7):
        wanted.append({"type": "local_audit", "title": "Audit Business Profiles and local rankings", "impact": 55, "effort": 1, "confidence": 0.85})
    period_days = {"daily": 1, "weekly": 7, "monthly": 30}.get(site.report_frequency)
    if period_days and _older(await _latest(db, SeoReport.created_at, SeoReport.site_id == sid, SeoReport.period == site.report_frequency),
                              period_days - 0.1):
        wanted.append({"type": "report", "title": f"{site.report_frequency.title()} progress report", "params": {"period": site.report_frequency},
                       "impact": 40, "effort": 1, "confidence": 0.95})
    approved = (await db.execute(select(func.count()).select_from(SeoLinkProspect).where(
        SeoLinkProspect.site_id == sid, SeoLinkProspect.status == "approved_for_outreach"))).scalar()
    if approved:
        wanted.append({"type": "link_outreach", "title": f"Reach out to {approved} approved link prospect(s)", "impact": 50, "effort": 2, "confidence": 0.6})
    pending_rows = (await db.execute(select(func.count()).select_from(SeoPseoRow).where(SeoPseoRow.site_id == sid, SeoPseoRow.generated.is_(False)))).scalar()
    approved_pages = (await db.execute(select(func.count()).select_from(SeoPseoPage).where(SeoPseoPage.site_id == sid, SeoPseoPage.status == "approved"))).scalar()
    if pending_rows or approved_pages:
        wanted.append({"type": "pseo_batch", "title": f"Generate and publish programmatic pages ({pending_rows} new, {approved_pages} approved)",
                       "impact": 50, "effort": 2, "confidence": 0.6})
    open_fixes = (await db.execute(select(func.count()).select_from(SeoTechnicalIssue).where(
        SeoTechnicalIssue.site_id == sid, SeoTechnicalIssue.status.in_(("open", "planned")), SeoTechnicalIssue.fix_plan.is_not(None)))).scalar()
    if open_fixes and "cms" in connected:
        wanted.append({"type": "technical_fix", "title": "Work through the open technical issues", "params": {"scope": "open_issues"},
                       "impact": 80, "effort": 2, "confidence": 0.8})

    queued = []
    for spec in wanted:
        task, created = await create_task(db, site, {**spec, "source": "autopilot"}, actor, commit=False)
        if created:
            queued.append(task.type)
    await db.commit()
    if stale.rowcount:
        queued.append(f"{stale.rowcount} stale claim(s) released")
    return queued


async def due_sites(db: AsyncSession, minutes: float) -> list[tuple[str, str]]:
    """Sites whose autopilot cycle is due."""
    cutoff = now() - timedelta(minutes=minutes)
    sites = (await db.execute(select(SeoSite).where(SeoSite.autopilot_mode != "off"))).scalars()
    return [(s.tenant_id, str(s.id)) for s in sites if s.last_cycle_at is None or aware(s.last_cycle_at) < cutoff]


async def mark_cycle(db: AsyncSession, tenant_id: str, site_id: str) -> bool:
    """Claims the site's next cycle (so only one replica runs it)."""
    cutoff = now() - timedelta(minutes=1)
    site = await get_site(db, tenant_id, site_id)
    result = await db.execute(update(SeoSite).where(SeoSite.id == site.id, (SeoSite.last_cycle_at.is_(None)) | (SeoSite.last_cycle_at < cutoff))
                              .values(last_cycle_at=now()))
    await db.commit()
    return bool(result.rowcount)


async def task_for_agent(db: AsyncSession, tenant_id: str, task_id: str) -> dict:
    task = await get_owned(db, SeoTask, tenant_id, task_id, "task")
    return row(task)
