"""Programmatic SEO: landing pages generated from a template and rows of
data. Each page is measured against its siblings and held — for a
person, never an agent, to clear — when it is thin, too similar to the
others, or trips the guardrail."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import (
    SeoError,
    activity,
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
from src.seo.constants import DEFAULT_CAPS
from src.seo.models import SeoPseoPage, SeoPseoRow, SeoPseoTemplate, SeoSite


async def create_template(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoPseoTemplate:
    site = await get_site(db, tenant_id, site_id)
    for field in ("name", "slug_pattern", "title_pattern", "body_template"):
        if not str(body.get(field) or "").strip():
            raise invalid(f"{field} is required")
    tpl = SeoPseoTemplate(tenant_id=tenant_id, site_id=site.id, name=body["name"][:256], slug_pattern=body["slug_pattern"][:256],
                          title_pattern=body["title_pattern"][:300], meta_pattern=str(body.get("meta_pattern") or "")[:512],
                          body_template=body["body_template"], required_slots=list(body.get("required_slots") or []),
                          min_words=int(body.get("min_words") or 300), min_unique_ratio=float(body.get("min_unique_ratio") or 0.3))
    db.add(tpl)
    await activity(db, site, "pseo", f"Template created: {tpl.name}", actor, "pseo_template", tpl.id)
    await db.commit()
    return tpl


async def add_rows(db: AsyncSession, tenant_id: str, template_id: str, rows_: list[dict], actor: str) -> dict:
    tpl = await get_owned(db, SeoPseoTemplate, tenant_id, template_id, "template")
    if not isinstance(rows_, list) or not rows_ or not all(isinstance(r, dict) for r in rows_):
        raise invalid("rows must be a non-empty list of objects")
    for r in rows_:
        db.add(SeoPseoRow(tenant_id=tenant_id, site_id=tpl.site_id, template_id=tpl.id, data=r))
    site = await get_site(db, tenant_id, tpl.site_id)
    await activity(db, site, "pseo", f"{len(rows_)} data row(s) added to {tpl.name}", actor, "pseo_template", tpl.id)
    await db.commit()
    return {"added": len(rows_)}


def _render(tpl: SeoPseoTemplate, data: dict) -> dict:
    body = rules.render_template(tpl.body_template, data)
    return {"slug": rules.slugify(rules.render_template(tpl.slug_pattern, data)), "title": rules.render_template(tpl.title_pattern, data),
            "meta_description": rules.render_template(tpl.meta_pattern, data), "body": body, "words": len(rules.words(body))}


async def preview(db: AsyncSession, tenant_id: str, template_id: str, data: dict) -> dict:
    tpl = await get_owned(db, SeoPseoTemplate, tenant_id, template_id, "template")
    return _render(tpl, data or {})


async def generate(db: AsyncSession, site: SeoSite, actor: str, limit: int = 50) -> dict:
    """Turns new data rows into pages, each checked against its siblings."""
    generated = held = 0
    for tpl in (await db.execute(select(SeoPseoTemplate).where(SeoPseoTemplate.site_id == site.id))).scalars():
        pending = list((await db.execute(select(SeoPseoRow).where(SeoPseoRow.template_id == tpl.id, SeoPseoRow.generated.is_(False))
                                         .order_by(SeoPseoRow.created_at).limit(limit))).scalars())
        if not pending:
            continue
        siblings = list((await db.execute(select(SeoPseoPage).where(SeoPseoPage.template_id == tpl.id))).scalars())
        bodies = [p.body_markdown for p in siblings]
        slugs = {p.slug for p in (await db.execute(select(SeoPseoPage).where(SeoPseoPage.site_id == site.id))).scalars()}
        for data_row in pending:
            data_row.generated = True
            missing = [s for s in tpl.required_slots or [] if not str(data_row.data.get(s) or "").strip()]
            page = _render(tpl, data_row.data)
            if page["slug"] in slugs:
                continue
            unique_ratio, max_sim = rules.uniqueness(page["body"], bodies)
            reasons = []
            if missing:
                reasons.append(f"missing data: {', '.join(missing)}")
            if page["words"] < tpl.min_words:
                reasons.append(f"thin content: {page['words']} words, the template requires at least {tpl.min_words}")
            if bodies and unique_ratio < tpl.min_unique_ratio:
                reasons.append(f"low uniqueness: only {round(unique_ratio * 100)}% of this page's text differs from its sibling pages "
                               f"(minimum {round(tpl.min_unique_ratio * 100)}%)")
            findings = rules.guardrail(f"{page['title']}\n{page['meta_description']}\n{page['body']}")
            status = "held" if reasons or findings else "approved"
            db.add(SeoPseoPage(tenant_id=site.tenant_id, site_id=site.id, template_id=tpl.id, row_id=data_row.id, slug=page["slug"],
                               title=page["title"][:300], meta_description=page["meta_description"][:512], body_markdown=page["body"],
                               words=page["words"], unique_ratio=unique_ratio if bodies else 1.0, max_similarity=max_sim,
                               status=status, hold_reasons=reasons, guardrail_findings=findings))
            bodies.append(page["body"])
            slugs.add(page["slug"])
            generated += 1
            held += status == "held"
    await activity(db, site, "pseo", f"Generated {generated} programmatic page(s), {held} held for review", actor)
    await db.commit()
    if generated:
        await emit("seo.pseo_generated", site, {"generated": generated, "held": held})
    return {"generated": generated, "held": held}


async def publish_batch(db: AsyncSession, tenant_id: str, template_id: str, limit: int, actor: str) -> dict:
    tpl = await get_owned(db, SeoPseoTemplate, tenant_id, template_id, "template")
    site = await get_site(db, tenant_id, tpl.site_id)
    cms = await connected(db, site, "cms") or await connected(db, site, "website_builder")
    if cms is None:
        raise conflict("the CMS integration is not connected; connect and test it before publishing")
    room = max(1, min(int(limit or 25), 100))
    if is_agent(actor):
        cap = (site.caps or DEFAULT_CAPS).get("max_pseo_pages_per_day", DEFAULT_CAPS["max_pseo_pages_per_day"])
        today = (await db.execute(select(func.count()).select_from(SeoPseoPage).where(
            SeoPseoPage.site_id == site.id, SeoPseoPage.published_at >= days_ago(1)))).scalar() or 0
        room = min(room, cap - today)
        if room <= 0:
            raise conflict(f"the daily programmatic page cap has been reached ({today}/{cap})")
    pages = list((await db.execute(select(SeoPseoPage).where(SeoPseoPage.template_id == tpl.id, SeoPseoPage.status.in_(("approved", "failed")))
                                   .order_by(SeoPseoPage.created_at).limit(room))).scalars())
    published = failed = 0
    for page in pages:
        if page.guardrail_findings and not page.reviewed_by:
            continue
        try:
            result = await adapters.publish(site, cms, {"title": page.title, "slug": page.slug, "body_markdown": page.body_markdown,
                                                        "meta_description": page.meta_description, "kind": "programmatic"})
            page.status, page.published_at, page.published_url, page.cms_ref, page.last_error = "published", now(), result["url"], result["ref"], None
            published += 1
        except SeoError as exc:
            page.status, page.last_error = "failed", exc.detail
            failed += 1
    remaining = (await db.execute(select(func.count()).select_from(SeoPseoPage).where(
        SeoPseoPage.template_id == tpl.id, SeoPseoPage.status == "approved"))).scalar() or 0
    await activity(db, site, "pseo", f"Published {published} programmatic page(s) ({failed} failed)", actor, "pseo_template", tpl.id)
    await db.commit()
    return {"published": published, "failed": failed, "remaining_approved": remaining}


async def approve_page(db: AsyncSession, tenant_id: str, page_id: str, actor: str) -> SeoPseoPage:
    if is_agent(actor):
        raise hard_gate("a held programmatic page can only be cleared by a person, never by an agent")
    page = await get_owned(db, SeoPseoPage, tenant_id, page_id, "page")
    if page.status != "held":
        raise conflict(f"page is {page.status}; only a held page needs a person's approval")
    page.status, page.reviewed_by = "approved", actor
    site = await get_site(db, tenant_id, page.site_id)
    await activity(db, site, "pseo", f"Held page cleared by {actor}: {page.slug}", actor, "pseo_page", page.id)
    await db.commit()
    return page


async def list_templates(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    out = []
    for tpl in (await db.execute(select(SeoPseoTemplate).where(SeoPseoTemplate.site_id == site.id))).scalars():
        pending = (await db.execute(select(func.count()).select_from(SeoPseoRow).where(SeoPseoRow.template_id == tpl.id,
                                                                                       SeoPseoRow.generated.is_(False)))).scalar()
        out.append({**row(tpl), "pending_rows": pending})
    return out


async def list_pages(db: AsyncSession, tenant_id: str, site_id: str, status: str | None) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoPseoPage).where(SeoPseoPage.site_id == site.id)
    if status:
        q = q.where(SeoPseoPage.status == status)
    return [row(p, exclude=("body_markdown",)) for p in (await db.execute(q.order_by(SeoPseoPage.created_at))).scalars()]


async def publishable_templates(db: AsyncSession, site: SeoSite) -> list[str]:
    ids = (await db.execute(select(SeoPseoPage.template_id).where(SeoPseoPage.site_id == site.id, SeoPseoPage.status == "approved")
                            .distinct())).scalars().all()
    return [str(i) for i in ids]
