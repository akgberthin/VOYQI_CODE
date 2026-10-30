"""SEO content: briefs, drafts scored for quality and checked by the
guardrail, scheduling and publishing through the CMS.

A draft with guardrail findings is held until a person clears it —
never an agent. Unattended publishing stops at the weekly cap."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, research, rules
from src.seo.common import (
    SeoError,
    activity,
    aware,
    conflict,
    connected,
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
from src.seo.constants import BANNED_PHRASES, CONTENT_KINDS, DEFAULT_CAPS, MIN_SEO_SCORE, TARGET_WORDS
from src.seo.models import SeoContent, SeoKeyword, SeoPageStat, SeoSite


def content_out(c: SeoContent, with_body: bool = False) -> dict:
    return row(c, exclude=() if with_body else ("body_markdown",))


async def brief(db: AsyncSession, tenant_id: str, site_id: str, keyword: str, kind: str | None = None) -> dict:
    """What a piece for this keyword must do: intent, format, outline,
    secondary keywords, questions to answer and pages to link."""
    site = await get_site(db, tenant_id, site_id)
    kw = (keyword or "").strip().lower()
    if not kw:
        raise invalid("keyword is required")
    if kind and kind not in CONTENT_KINDS:
        raise invalid(f"kind must be one of {CONTENT_KINDS}")
    intent = await research.search_intent(db, tenant_id, site_id, kw)
    keywords = list((await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id))).scalars())
    match = next((k for k in keywords if k.term == kw), None)
    cluster = match.cluster if match else ""
    secondary = list(dict.fromkeys(k.term for k in keywords if cluster and k.cluster == cluster and k.term != kw and not k.is_question))[:8]
    want = rules.stems(kw)
    questions = list(dict.fromkeys(k.term for k in keywords if k.is_question and
                                   ((cluster and k.cluster == cluster) or rules.jaccard(rules.stems(k.term), want) >= 0.4)))[:8]
    published = (await db.execute(select(SeoContent).where(SeoContent.site_id == site.id, SeoContent.status == "published",
                                                           SeoContent.published_url.is_not(None)))).scalars()
    pages = (await db.execute(select(SeoPageStat).where(SeoPageStat.site_id == site.id, SeoPageStat.window == "current"))).scalars()
    candidates = [(c.title, c.published_url, f"{c.target_keyword} {c.title}") for c in published]
    candidates += [(p.top_query or p.url, p.url, f"{p.top_query or ''} {p.url}") for p in pages]
    links, seen = [], set()
    for title, url, text in candidates:
        rel = round(rules.jaccard(rules.stems(text), want), 2)
        if rel > 0 and url not in seen:
            seen.add(url)
            links.append({"title": title, "url": url, "relevance": rel})
    links.sort(key=lambda x: -x["relevance"])
    chosen = kind or intent["recommended_content"]["kind"]
    title_case = kw.title()
    must = intent["recommended_content"]["must_include"]
    disclosure = rules.FINANCE_RE.search(" ".join([kw, *secondary, *questions]))
    return {
        "keyword": kw, "kind": chosen, "intent": intent["intent"], "purchase_intent": intent["purchase_intent"],
        "format": intent["dominant_format"], "angle": intent["recommended_content"]["angle"], "must_include": must,
        "secondary_keywords": secondary, "questions_to_answer": questions, "internal_link_targets": links[:5],
        "outline": [f"# {title_case}: {'What to know' if intent['intent'] == 'informational' else 'Everything you need to decide'}",
                    *[f"## {m[0].upper() + m[1:]}" for m in must[:3]],
                    *[f"## {q.rstrip('?')[0].upper() + q.rstrip('?')[1:]}?" for q in questions[:3]],
                    "## Frequently asked questions"],
        "target_words": TARGET_WORDS.get(chosen, 800),
        "require_disclosure": "subject to credit approval" if disclosure else None,
        "banned_phrases": BANNED_PHRASES, "evidence_confidence": intent["confidence"],
        "brand": {"name": site.name, "domain": site.domain, **{k: (site.business_profile or {}).get(k) for k in ("description", "positioning", "products", "markets")}},
    }


async def create(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoContent:
    site = await get_site(db, tenant_id, site_id)
    kind = body.get("kind") or "blog"
    if kind not in CONTENT_KINDS:
        raise invalid("kind must be one of the content kinds")
    keyword = str(body.get("target_keyword") or "").strip().lower()
    if not keyword:
        raise invalid("target_keyword is required")
    open_same = (await db.execute(select(SeoContent).where(SeoContent.site_id == site.id, SeoContent.target_keyword == keyword,
                                                           SeoContent.status.notin_(("published", "failed"))))).scalars().first()
    if open_same and not body.get("refresh_of_url"):
        return open_same
    item = SeoContent(tenant_id=tenant_id, site_id=site.id, kind="refresh" if body.get("refresh_of_url") else kind,
                      target_keyword=keyword[:300], title=str(body.get("title") or "")[:300],
                      meta_description=str(body.get("meta_description") or "")[:512],
                      slug=rules.slugify(body.get("title") or keyword), brief=body.get("brief") or {}, status="planned",
                      refresh_of_url=body.get("refresh_of_url"), created_by=actor)
    db.add(item)
    await db.flush()
    await activity(db, site, "content", f"Content planned: {keyword[:100]}", actor, "content", item.id)
    await db.commit()
    return item


async def get(db: AsyncSession, tenant_id: str, content_id: str) -> SeoContent:
    return await get_owned(db, SeoContent, tenant_id, content_id, "content item")


async def list_content(db: AsyncSession, tenant_id: str, site_id: str, status: str | None, kind: str | None) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoContent).where(SeoContent.site_id == site.id)
    if status:
        q = q.where(SeoContent.status == status)
    if kind:
        q = q.where(SeoContent.kind == kind)
    return [content_out(c) for c in (await db.execute(q.order_by(SeoContent.created_at.desc()))).scalars()]


async def submit_draft(db: AsyncSession, tenant_id: str, content_id: str, body: dict, actor: str) -> SeoContent:
    """A written draft: scored, checked, and then approved, sent back for
    revision, or held for a person."""
    item = await get(db, tenant_id, content_id)
    if item.status in ("published",) and not item.refresh_of_url:
        raise conflict("this piece is already published; plan a refresh instead")
    title = str(body.get("title") or item.title or "").strip()[:300]
    meta = str(body.get("meta_description") or "").strip()[:512]
    text = str(body.get("body_markdown") or "")
    if not title or not text.strip():
        raise invalid("title and body_markdown are required")
    score, quality = rules.content_quality(item.kind, item.target_keyword, title, meta, text)
    findings = rules.guardrail(f"{title}\n{meta}\n{text}")
    if body.get("written_by") == "template":
        # No AI writer was available: generic copy never goes live unseen.
        findings.append("written from a template because no AI writer is configured: a person should edit it before it is published")
    if item.status not in ("planned", "drafting"):
        item.revision += 1
    item.title, item.meta_description, item.body_markdown = title, meta, text
    if not item.refresh_of_url:
        item.slug = rules.slugify(body.get("slug") or title)[:160]
    item.seo_score, item.quality_findings, item.guardrail_findings = score, quality, findings
    item.reviewed_by, item.last_error = None, None
    item.status = "held_for_review" if findings else ("needs_revision" if score < MIN_SEO_SCORE else "approved")
    site = await get_site(db, tenant_id, item.site_id)
    note = {"held_for_review": f"held for a person ({len(findings)} guardrail finding(s))",
            "needs_revision": f"needs revision (score {score}, minimum {MIN_SEO_SCORE})", "approved": f"approved (score {score})"}[item.status]
    await activity(db, site, "content", f"Draft r{item.revision} of '{title[:80]}': {note}", actor, "content", item.id)
    await db.commit()
    await emit("seo.content_created", site, {"content_id": item.id, "status": item.status, "score": score})
    return item


async def approve(db: AsyncSession, tenant_id: str, content_id: str, actor: str) -> SeoContent:
    if is_agent(actor):
        raise hard_gate("held content can only be cleared by a person, never by an agent")
    item = await get(db, tenant_id, content_id)
    if item.status not in ("held_for_review", "needs_revision"):
        raise conflict(f"content is {item.status}; only held content needs a person's approval")
    item.status, item.reviewed_by = "approved", actor
    site = await get_site(db, tenant_id, item.site_id)
    await activity(db, site, "content", f"Held content approved by {actor}: {item.title[:90]}", actor, "content", item.id)
    await db.commit()
    return item


async def schedule_slots(db: AsyncSession, site: SeoSite, count: int) -> list[datetime]:
    """The next free weekday 09:00 UTC slots."""
    taken = {aware(c.publish_at).date() for c in (await db.execute(select(SeoContent).where(
        SeoContent.site_id == site.id, SeoContent.status == "scheduled", SeoContent.publish_at.is_not(None)))).scalars()}
    slots, day = [], now().date() + timedelta(days=1)
    while len(slots) < count and len(slots) < 30:
        if day.weekday() < 5 and day not in taken:
            slots.append(datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc))
        day += timedelta(days=1)
    return slots


async def schedule(db: AsyncSession, tenant_id: str, content_id: str, body: dict, actor: str) -> SeoContent:
    item = await get(db, tenant_id, content_id)
    if item.status not in ("approved", "scheduled"):
        raise conflict(f"content is {item.status}; only an approved piece can be scheduled")
    site = await get_site(db, tenant_id, item.site_id)
    when = body.get("publish_at")
    if when:
        try:
            publish_at = aware(datetime.fromisoformat(str(when).replace("Z", "+00:00")))
        except ValueError as exc:
            raise invalid("publish_at must be an ISO-8601 time") from exc
    else:
        publish_at = (await schedule_slots(db, site, 1))[0]
    item.status, item.publish_at = "scheduled", publish_at
    await activity(db, site, "content", f"Scheduled for {publish_at:%Y-%m-%d %H:%M} UTC: {item.title[:90]}", actor, "content", item.id)
    await db.commit()
    return item


async def published_this_week(db: AsyncSession, site: SeoSite) -> int:
    return int((await db.execute(select(func.count()).select_from(SeoContent).where(
        SeoContent.site_id == site.id, SeoContent.published_at >= days_ago(7)))).scalar() or 0)


async def publish(db: AsyncSession, tenant_id: str, content_id: str, actor: str, unattended: bool | None = None) -> SeoContent:
    item = await get(db, tenant_id, content_id)
    site = await get_site(db, tenant_id, item.site_id)
    if item.status not in ("approved", "scheduled", "failed"):
        raise conflict(f"content is {item.status}; only an approved or scheduled piece can be published")
    if item.guardrail_findings and not item.reviewed_by:
        raise hard_gate("this piece has guardrail findings and has not been cleared by a person")
    unattended = is_agent(actor) if unattended is None else unattended
    cap = (site.caps or DEFAULT_CAPS).get("max_publish_per_week", DEFAULT_CAPS["max_publish_per_week"])
    if unattended and await published_this_week(db, site) >= cap:
        raise conflict("the weekly publishing cap has been reached; unattended publishing resumes next week")
    cms = await connected(db, site, "cms") or await connected(db, site, "website_builder")
    if cms is None:
        raise conflict("the CMS integration is not connected; connect and test it before publishing")
    try:
        result = await adapters.publish(site, cms, {"title": item.title, "slug": item.slug, "body_markdown": item.body_markdown,
                                                    "meta_description": item.meta_description, "kind": item.kind,
                                                    "refresh_of_url": item.refresh_of_url})
    except SeoError as exc:
        item.status, item.last_error = "failed", exc.detail
        await activity(db, site, "content", f"Publishing failed: {item.title[:80]} — {exc.detail[:200]}", actor, "content", item.id)
        await db.commit()
        raise
    item.status, item.published_at, item.published_url, item.cms_ref, item.last_error = "published", now(), result["url"], result["ref"], None
    await activity(db, site, "content", f"{'Refreshed' if item.refresh_of_url else 'Published'}: {item.title[:90]}", actor, "content", item.id)
    await db.commit()
    await emit("seo.content_published", site, {"content_id": item.id, "url": item.published_url})
    return item


async def publish_due(session_factory) -> int:
    """Scheduler: publishes scheduled pieces whose time has come (an
    unattended publish, so the weekly cap applies)."""
    async with session_factory() as db:
        due = [(c.tenant_id, str(c.id)) for c in (await db.execute(select(SeoContent).where(
            SeoContent.status == "scheduled", SeoContent.publish_at <= now()))).scalars()]
    done = 0
    for tenant_id, content_id in due:
        async with session_factory() as db:
            try:
                await publish(db, tenant_id, content_id, "scheduler", unattended=True)
                done += 1
            except SeoError as exc:
                if exc.code == "conflict" and "cap" in exc.detail:
                    item = await get(db, tenant_id, content_id)
                    item.publish_at = now() + timedelta(days=1)
                    await db.commit()
    return done


async def refresh_candidates(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    stats = list((await db.execute(select(SeoPageStat).where(SeoPageStat.site_id == site.id))).scalars())
    if not stats:
        return {"candidates": [], "has_search_console_data": False,
                "note": "connect Search Console: refresh candidates come from real clicks and positions"}
    previous = {s.url: s for s in stats if s.window == "previous"}
    content = {c.published_url: c for c in (await db.execute(select(SeoContent).where(SeoContent.site_id == site.id,
                                                                                      SeoContent.published_url.is_not(None)))).scalars()}
    out = []
    for cur in (s for s in stats if s.window == "current"):
        prev = previous.get(cur.url)
        reasons, score = [], 0
        if prev and prev.clicks >= 50 and cur.clicks <= prev.clicks * 0.8:
            drop = round((prev.clicks - cur.clicks) / prev.clicks * 100)
            reasons.append({"code": "traffic_decay", "detail": f"clicks fell {drop}% ({prev.clicks} → {cur.clicks}) over the last 28 days"})
            score += 40
        if prev and prev.position and cur.position and cur.position - prev.position >= 2:
            reasons.append({"code": "position_slip", "detail": f"average position slipped from {prev.position:.1f} to {cur.position:.1f}"})
            score += 16
        if cur.position and 4 <= cur.position <= 20 and cur.impressions >= 100:
            reasons.append({"code": "striking_distance", "detail": f"averages position {cur.position:.1f} with {cur.impressions} impressions: "
                                                                   "a refresh can reach page one"})
            score += 20
        if reasons:
            c = content.get(cur.url)
            out.append({"url": cur.url, "content_id": str(c.id) if c else None, "title": c.title if c else None,
                        "keyword": (c.target_keyword if c else None) or cur.top_query, "score": score, "reasons": reasons})
    out.sort(key=lambda x: -x["score"])
    return {"candidates": out[:25], "has_search_console_data": True, "note": None}


async def schedule_summary(db: AsyncSession, tenant_id: str, site_id: str) -> dict:
    site = await get_site(db, tenant_id, site_id)
    items = list((await db.execute(select(SeoContent).where(SeoContent.site_id == site.id))).scalars())
    target = (site.caps or DEFAULT_CAPS).get("max_publish_per_week", DEFAULT_CAPS["max_publish_per_week"])
    week = days_ago(7)
    published = sum(1 for c in items if c.published_at and aware(c.published_at) >= week)
    by_status: dict[str, int] = {}
    for c in items:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    remaining = max(0, target - published)
    upcoming = sorted((c for c in items if c.status == "scheduled" and c.publish_at), key=lambda c: aware(c.publish_at))
    return {"weekly_target": target, "published_last_7d": published, "remaining_this_week": remaining, "by_status": by_status,
            "recommended_slots": [s.isoformat() for s in await schedule_slots(db, site, remaining)],
            "upcoming": [{"id": str(c.id), "title": c.title, "publish_at": aware(c.publish_at).isoformat()} for c in upcoming[:10]]}
