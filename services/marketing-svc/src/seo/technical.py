"""Technical SEO: crawl audits, the issues they find, and fixes applied
through the CMS under the fix policy — low-risk reversible fixes may be
applied by an agent (within the daily cap); everything else waits for a
person."""
import hashlib

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
from src.seo.constants import DEFAULT_CAPS, FIX_POLICY
from src.seo.models import SeoLocation, SeoSite, SeoTechnicalAudit, SeoTechnicalIssue

OPEN = ("open", "awaiting_approval", "planned", "failed")
SEVERITY_WEIGHT = {"critical": 8, "warning": 3, "info": 1}


def fingerprint(code: str, url: str) -> str:
    return hashlib.sha1(f"{code}|{url}".encode()).hexdigest()


async def run_audit(db: AsyncSession, site: SeoSite, actor: str) -> dict:
    integ = await connected(db, site, "crawler")
    if integ is None:
        raise SeoError(409, "the site crawler integration is not connected", "provider_not_configured", kind="crawler")
    result = await adapters.crawl(site, integ)
    audit = SeoTechnicalAudit(tenant_id=site.tenant_id, site_id=site.id, source=f"{integ.provider} crawl",
                              pages_scanned=result["pages_scanned"], issue_counts={}, health_score=0)
    db.add(audit)
    await db.flush()
    existing = {i.fingerprint: i for i in (await db.execute(select(SeoTechnicalIssue).where(SeoTechnicalIssue.site_id == site.id))).scalars()}
    seen, new, regressed = set(), 0, 0
    for found in result["issues"]:
        fp = fingerprint(found["code"], found["url"])
        if fp in seen:
            continue
        seen.add(fp)
        plan = rules.fix_plan_for(found["fix_type"], found["fix_params"]) if found["fix_type"] else None
        issue = existing.get(fp)
        if issue is None:
            issue = SeoTechnicalIssue(tenant_id=site.tenant_id, site_id=site.id, fingerprint=fp, category=found["category"],
                                      code=found["code"], url=found["url"][:1024], status="open")
            db.add(issue)
            new += 1
        elif issue.status in ("fixed", "resolved"):
            issue.status, issue.fixed_at, issue.fix_result = "open", None, None
            regressed += 1
        if issue.status == "dismissed":
            continue
        issue.audit_id, issue.severity, issue.detail, issue.evidence = audit.id, found["severity"], found["detail"], found["evidence"]
        issue.fix_plan, issue.last_seen_at = plan, now()
        issue.auto_fixable, issue.risk = bool(plan and plan["auto_applicable"]), (plan or {}).get("risk", "low")
        if issue.status == "open" and plan and not plan["auto_applicable"] and not issue.approved_by:
            issue.status = "awaiting_approval"
    resolved = 0
    for fp, issue in existing.items():
        if fp not in seen and issue.status in OPEN:
            issue.status = "resolved"
            resolved += 1
    await db.flush()
    counts: dict[str, int] = {}
    for sev, n in (await db.execute(select(SeoTechnicalIssue.severity, func.count()).where(
            SeoTechnicalIssue.site_id == site.id, SeoTechnicalIssue.status.in_(OPEN)).group_by(SeoTechnicalIssue.severity))).all():
        counts[sev] = n
    audit.issue_counts = counts
    audit.health_score = int(rules.clamp(100 - sum(SEVERITY_WEIGHT.get(s, 1) * n for s, n in counts.items())))
    await activity(db, site, "technical_fix", f"Audit of {audit.pages_scanned} page(s): health {audit.health_score}/100, {new} new, "
                   f"{regressed} back again, {resolved} resolved", actor, "audit", audit.id)
    await db.commit()
    await emit("seo.audit_completed", site, {"audit_id": audit.id, "health_score": audit.health_score, "issue_counts": counts})
    return {**row(audit), "new": new, "regressed": regressed, "resolved": resolved, "vitals": result.get("vitals", {})}


async def list_audits(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(a) for a in (await db.execute(select(SeoTechnicalAudit).where(SeoTechnicalAudit.site_id == site.id)
                                              .order_by(SeoTechnicalAudit.created_at.desc()).limit(20))).scalars()]


async def list_issues(db: AsyncSession, tenant_id: str, site_id: str, f: dict) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    q = select(SeoTechnicalIssue).where(SeoTechnicalIssue.site_id == site.id)
    for field in ("status", "category", "severity"):
        if f.get(field):
            q = q.where(getattr(SeoTechnicalIssue, field) == f[field])
    if not f.get("status"):
        q = q.where(SeoTechnicalIssue.status != "resolved")
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted((row(i) for i in (await db.execute(q)).scalars()), key=lambda i: (order.get(i["severity"], 3), i["code"]))


async def _schema_for(db: AsyncSession, site: SeoSite, params: dict, body: dict) -> dict:
    if body.get("jsonld"):
        problems = rules.validate_schema(body["jsonld"])
        if problems:
            raise invalid("the structured data is not valid: " + "; ".join(problems), problems=problems)
        return body["jsonld"]
    schema_type = params.get("schema_type")
    if schema_type == "Organization":
        return rules.build_schema("Organization", {"name": site.name, "url": f"https://{site.domain}/",
                                                   "description": (site.business_profile or {}).get("description")})
    if schema_type == "LocalBusiness":
        loc = (await db.execute(select(SeoLocation).where(SeoLocation.site_id == site.id, SeoLocation.website == params.get("url")))).scalars().first()
        if loc is None:
            raise invalid("no location is linked to this page; add the location with this website URL first")
        return rules.build_schema("LocalBusiness", {"name": loc.name, "address": loc.address, "telephone": loc.phone, "url": loc.website})
    return rules.build_schema(schema_type, body.get("entity") or {})


async def fix(db: AsyncSession, tenant_id: str, issue_id: str, body: dict, actor: str) -> dict:
    issue = await get_owned(db, SeoTechnicalIssue, tenant_id, issue_id, "issue")
    site = await get_site(db, tenant_id, issue.site_id)
    if issue.status in ("fixed", "dismissed", "resolved"):
        raise conflict(f"issue is already {issue.status}")
    plan = issue.fix_plan or {}
    if not plan.get("fix_type"):
        raise conflict("this issue has no automatic fix; it needs a person")
    agent = is_agent(actor)
    if not plan.get("auto_applicable") and not issue.approved_by:
        if agent:
            raise hard_gate(f"'{plan['fix_type']}' needs a person's approval before it is applied ({plan.get('reason')})")
        issue.approved_by = actor  # a person applying it is the approval
    if agent:
        cap = (site.caps or DEFAULT_CAPS).get("max_auto_fixes_per_day", DEFAULT_CAPS["max_auto_fixes_per_day"])
        used = (await db.execute(select(func.count()).select_from(SeoTechnicalIssue).where(
            SeoTechnicalIssue.site_id == site.id, SeoTechnicalIssue.fixed_at >= days_ago(1)))).scalar() or 0
        if used >= cap:
            raise conflict(f"the daily auto-fix cap has been reached ({used}/{cap})")
    cms = await connected(db, site, "cms") or await connected(db, site, "website_builder")
    if cms is None:
        raise conflict("the CMS integration is not connected; connect and test it before applying fixes")
    params = dict(plan.get("params") or {})
    if params.get("needs_generation"):
        if plan["fix_type"] == "add_alt_text":
            if not body.get("alt_texts"):
                raise invalid("alt_texts ({image url: alt text}) is required for this fix")
            params["alt_texts"] = body["alt_texts"]
        else:
            value = str(body.get("value") or "").strip()
            if not value:
                raise invalid("value (the new text) is required for this fix")
            if rules.guardrail(value):
                raise invalid("the new text trips the guardrail: " + "; ".join(rules.guardrail(value)))
            params["value"] = value
    if plan["fix_type"] == "add_schema":
        params["jsonld"] = await _schema_for(db, site, params, body)
    try:
        detail = await adapters.apply_fix(site, cms, plan["fix_type"], params)
    except SeoError as exc:
        issue.status, issue.fix_result = "failed", {"applied": False, "detail": exc.detail, "applied_by": actor}
        await activity(db, site, "technical_fix", f"Fix failed: {issue.code} on {issue.url} — {exc.detail[:200]}", actor, "issue", issue.id)
        await db.commit()
        raise
    issue.status, issue.fixed_at = "fixed", now()
    issue.fix_result = {"applied": True, "detail": detail, "applied_by": actor,
                        **({"value": params["value"]} if "value" in params else {})}
    await activity(db, site, "technical_fix", f"Fixed: {issue.code} on {issue.url} ({plan['fix_type']}: {detail})", actor, "issue", issue.id)
    await db.commit()
    await emit("seo.fix_applied", site, {"issue_id": issue.id, "fix_type": plan["fix_type"]})
    return {"applied": True, "held": False, "reason": None, "result": issue.fix_result, "issue": row(issue)}


async def approve(db: AsyncSession, tenant_id: str, issue_id: str, actor: str) -> SeoTechnicalIssue:
    if is_agent(actor):
        raise hard_gate("a technical fix that needs approval can only be approved by a person")
    issue = await get_owned(db, SeoTechnicalIssue, tenant_id, issue_id, "issue")
    if issue.status != "awaiting_approval":
        raise conflict(f"issue is {issue.status}; only an issue awaiting approval can be approved")
    issue.status, issue.approved_by = "planned", actor
    site = await get_site(db, tenant_id, issue.site_id)
    await activity(db, site, "technical_fix", f"Fix approved by {actor}: {issue.code} on {issue.url}", actor, "issue", issue.id)
    await db.commit()
    return issue


async def dismiss(db: AsyncSession, tenant_id: str, issue_id: str, actor: str) -> SeoTechnicalIssue:
    issue = await get_owned(db, SeoTechnicalIssue, tenant_id, issue_id, "issue")
    issue.status = "dismissed"
    site = await get_site(db, tenant_id, issue.site_id)
    await activity(db, site, "technical_fix", f"Dismissed: {issue.code} on {issue.url}", actor, "issue", issue.id)
    await db.commit()
    return issue


def fix_policy() -> dict:
    return FIX_POLICY


async def fixable(db: AsyncSession, site: SeoSite, agent: bool) -> list[SeoTechnicalIssue]:
    """Issues an agent may work on now: auto-applicable or approved."""
    issues = (await db.execute(select(SeoTechnicalIssue).where(SeoTechnicalIssue.site_id == site.id,
                                                              SeoTechnicalIssue.status.in_(("open", "planned", "failed"))))).scalars()
    order = {"critical": 0, "warning": 1, "info": 2}
    out = [i for i in issues if i.fix_plan and i.fix_plan.get("fix_type") and (i.fix_plan.get("auto_applicable") or i.approved_by or not agent)]
    return sorted(out, key=lambda i: order.get(i.severity, 3))
