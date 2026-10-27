"""Sites, their autonomy settings, their integrations and the activity
feed."""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules, tasks
from src.seo.common import (
    SeoError,
    activity,
    emit,
    get_site,
    integration,
    integration_out,
    invalid,
    now,
    row,
)
from src.seo.constants import AUTOPILOT_MODES, CAPS, DEFAULT_CAPS, INTEGRATION_KINDS, REPORT_FREQUENCIES, TASK_TYPES
from src.seo.models import SeoActivity, SeoCompetitor, SeoSite

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def list_sites(db: AsyncSession, tenant_id: str) -> list[SeoSite]:
    return list((await db.execute(select(SeoSite).where(SeoSite.tenant_id == tenant_id).order_by(SeoSite.created_at))).scalars())


async def create_site(db: AsyncSession, tenant_id: str, body: dict, actor: str) -> SeoSite:
    name = str(body.get("name") or "").strip()
    domain = rules.normalize_domain(body.get("domain") or "")
    if not name:
        raise invalid("name is required")
    if not rules.DOMAIN_RE.match(domain):
        raise invalid(f"'{body.get('domain')}' is not a valid domain")
    exists = (await db.execute(select(SeoSite).where(SeoSite.tenant_id == tenant_id, SeoSite.domain == domain))).scalar_one_or_none()
    if exists:
        raise SeoError(409, f"{domain} is already registered", "conflict")
    profile = body.get("business_profile") or {}
    profile.setdefault("brand_terms", [domain.split(".")[0]])
    site = SeoSite(tenant_id=tenant_id, name=name, domain=domain, business_profile=profile, goals=body.get("goals") or {},
                   caps=dict(DEFAULT_CAPS), autopilot_mode="assisted")
    db.add(site)
    await db.flush()
    for kind in INTEGRATION_KINDS:
        await integration(db, site, kind)
    for comp in body.get("competitors") or []:
        d = rules.normalize_domain(comp)
        if rules.DOMAIN_RE.match(d) and d != domain:
            db.add(SeoCompetitor(tenant_id=tenant_id, site_id=site.id, domain=d, notes="added at setup"))
    await activity(db, site, "strategy", f"Site registered: {domain}", actor, "site", site.id)
    await db.commit()
    await emit("seo.site_registered", site, {"domain": domain})
    return site


async def update_site(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoSite:
    site = await get_site(db, tenant_id, site_id)
    if body.get("autopilot_mode") is not None:
        if body["autopilot_mode"] not in AUTOPILOT_MODES:
            raise invalid(f"autopilot_mode must be one of {AUTOPILOT_MODES}")
        site.autopilot_mode = body["autopilot_mode"]
    if body.get("report_frequency"):
        if body["report_frequency"] not in REPORT_FREQUENCIES:
            raise invalid("report_frequency is invalid")
        site.report_frequency = body["report_frequency"]
    if body.get("caps"):
        caps = dict(site.caps or DEFAULT_CAPS)
        for key, value in body["caps"].items():
            if key not in CAPS:
                raise invalid(f"unknown cap '{key}'")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise invalid(f"cap '{key}' must be a non-negative integer")
            caps[key] = value
        site.caps = caps
    if body.get("autonomy_overrides") is not None:
        for key, value in body["autonomy_overrides"].items():
            if key not in TASK_TYPES:
                raise invalid(f"unknown task type '{key}'")
            if value not in ("auto", "approval"):
                raise invalid("an autonomy override must be 'auto' or 'approval'")
        site.autonomy_overrides = dict(body["autonomy_overrides"])
    for field in ("authorized_requesters", "report_recipients"):
        if body.get(field) is not None:
            cleaned: list[str] = []
            for raw in body[field]:
                email = str(raw).strip().lower()
                if not EMAIL_RE.match(email):
                    raise invalid(f"'{email}' is not a valid email address")
                if email not in cleaned:
                    cleaned.append(email)
            setattr(site, field, cleaned)
    for field in ("name",):
        if body.get(field):
            setattr(site, field, str(body[field]).strip())
    for field in ("business_profile", "goals"):
        if body.get(field) is not None:
            if not isinstance(body[field], dict):
                raise invalid(f"{field} must be an object")
            setattr(site, field, {**(getattr(site, field) or {}), **body[field]})
    changed = ", ".join(sorted(k for k, v in body.items() if v is not None))
    await activity(db, site, "strategy", f"Site settings updated ({changed})", actor, "site", site.id)
    await db.commit()
    await db.refresh(site)
    await emit("seo.site_updated", site, {"changed": changed})
    return site


# ── integrations ──────────────────────────────────────────────────

async def list_integrations(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    out = [integration_out(await integration(db, site, kind)) for kind in INTEGRATION_KINDS]
    await db.commit()
    return out


def _check_kind(kind: str) -> None:
    if kind not in INTEGRATION_KINDS:
        raise invalid(f"unknown integration kind '{kind}'")


async def configure_integration(db: AsyncSession, tenant_id: str, site_id: str, kind: str, body: dict, actor: str) -> dict:
    _check_kind(kind)
    site = await get_site(db, tenant_id, site_id)
    provider = str(body.get("provider") or "").strip()
    if not provider:
        raise invalid("provider is required")
    problem = rules.secret_in_config(body.get("config") or {})
    if problem:
        raise invalid(problem)
    ref = body.get("credential_ref") or None
    if ref and rules.looks_like_secret(ref):
        raise invalid("credential_ref must be a reference (e.g. 'rbac://dataforseo'), not the credential itself")
    integ = await integration(db, site, kind)
    integ.provider, integ.credential_ref, integ.config = provider, ref, body.get("config") or {}
    integ.status, integ.last_error = "configured", None
    label = INTEGRATION_KINDS[kind][0]
    await activity(db, site, "integration", f"{label} configured with provider '{provider}' (not yet tested)", actor, "integration", kind)
    await db.commit()
    await emit("seo.integration_configured", site, {"kind": kind, "provider": provider})
    return integration_out(integ)


async def test_integration(db: AsyncSession, tenant_id: str, site_id: str, kind: str, actor: str) -> dict:
    _check_kind(kind)
    site = await get_site(db, tenant_id, site_id)
    integ = await integration(db, site, kind)
    if integ.status == "not_connected":
        raise SeoError(404, f"no '{kind}' integration is configured for this site", "not_found")
    label = INTEGRATION_KINDS[kind][0]
    integ.last_tested_at = now()
    try:
        detail = await adapters.test(site, integ)
    except SeoError as exc:
        integ.status, integ.last_error = "error", exc.detail
        await activity(db, site, "integration", f"{label} test failed: {exc.detail}", actor, "integration", kind)
        await db.commit()
        raise
    integ.status, integ.last_error = "connected", None
    await activity(db, site, "integration", f"{label} test passed — {detail}", actor, "integration", kind)
    await tasks.unblock(db, site)
    await db.commit()
    await emit("seo.integration_tested", site, {"kind": kind, "provider": integ.provider})
    return {**integration_out(integ), "detail": detail}


async def disconnect_integration(db: AsyncSession, tenant_id: str, site_id: str, kind: str, actor: str) -> dict:
    _check_kind(kind)
    site = await get_site(db, tenant_id, site_id)
    integ = await integration(db, site, kind)
    integ.status, integ.provider, integ.credential_ref, integ.config, integ.last_error = "not_connected", None, None, {}, None
    await activity(db, site, "integration", f"{INTEGRATION_KINDS[kind][0]} disconnected", actor, "integration", kind)
    await db.commit()
    return {"disconnected": True}


# ── competitors and activity ──────────────────────────────────────

async def add_competitor(db: AsyncSession, tenant_id: str, site_id: str, domain: str, notes: str, actor: str) -> SeoCompetitor:
    site = await get_site(db, tenant_id, site_id)
    d = rules.normalize_domain(domain)
    if not rules.DOMAIN_RE.match(d) or d == site.domain:
        raise invalid(f"'{domain}' is not a valid competitor domain")
    found = (await db.execute(select(SeoCompetitor).where(SeoCompetitor.site_id == site.id, SeoCompetitor.domain == d))).scalar_one_or_none()
    if found:
        return found
    comp = SeoCompetitor(tenant_id=tenant_id, site_id=site.id, domain=d, notes=notes)
    db.add(comp)
    await activity(db, site, "research", f"Competitor tracked: {d}", actor, "competitor", d)
    await db.commit()
    return comp


async def list_activity(db: AsyncSession, tenant_id: str, site_id: str, kind: str | None, limit: int) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoActivity).where(SeoActivity.site_id == site.id)
    if kind:
        q = q.where(SeoActivity.kind == kind)
    rows = (await db.execute(q.order_by(SeoActivity.created_at.desc()).limit(max(1, min(limit, 500))))).scalars()
    return [row(a) for a in rows]
