"""Conversion rate optimisation: page reviews that find what stops
visitors converting, and A/B experiments decided by a two-proportion
z-test — never called early."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import adapters, rules
from src.seo.common import SeoError, activity, conflict, emit, get_owned, get_site, invalid, now, parse_id, row
from src.seo.models import SeoCroExperiment, SeoCroFinding, SeoKeyword, SeoSite, SeoTechnicalIssue

MIN_PER_ARM = 100
ALPHA = 0.05

RECOMMEND = {
    "no_cta_above_fold": ("critical", "Add one clear primary call to action in the first screen.",
                          "Visitors who cannot see the next step often leave without taking it.", "Test a hero CTA against the current page."),
    "not_mobile_friendly": ("critical", "Fix the mobile layout before running any other test.", "Most organic visits arrive on mobile.", "None: fix first."),
    "form_too_long": ("warning", "Ask only for what you need now; collect the rest later.", "Each extra field loses some visitors.", "Test a shorter form."),
    "no_trust_signals": ("warning", "Add genuine member reviews or recognised badges beside the CTA.",
                         "Reassurance at the decision point reduces hesitation.", "Test with and without social proof."),
    "no_pricing_info": ("info", "Show a starting price or plan summary.", "Searchers with buying intent are comparing costs.", "Test adding a price summary."),
    "intent_mismatch": ("warning", "Put the target topic in the headline and make the next step match the intent.",
                        "Pages that match what was searched convert better and rank better.", "Rewrite the headline and hero for the query."),
    "slow_for_conversion": ("warning", "Speed up the first screen.", "Slow pages lose visitors before they see the offer.",
                            "Compare conversion before and after a speed fix."),
    "weak_cta": ("info", "Make the call to action verb-first and specific to the offer.", "Generic labels like 'Submit' or 'Learn more' undersell the step.",
                 "Test one of the suggested CTA variants."),
}


async def _pages_to_review(db: AsyncSession, site: SeoSite, url: str | None) -> list[tuple[str, SeoKeyword | None]]:
    if url:
        kw = (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.url == url)
                               .order_by(SeoKeyword.volume.desc().nullslast()))).scalars().first()
        return [(url, kw)]
    buying = (await db.execute(select(SeoKeyword).where(SeoKeyword.site_id == site.id, SeoKeyword.owner == site.domain,
                                                        SeoKeyword.url.is_not(None), SeoKeyword.purchase_intent >= 55)
                               .order_by(SeoKeyword.opportunity.desc()).limit(20))).scalars()
    pages: dict[str, SeoKeyword] = {}
    for k in buying:
        pages.setdefault(k.url, k)
    out = [(u, k) for u, k in list(pages.items())[:4]]
    home = f"https://{site.domain}/"
    if home not in pages:
        out.insert(0, (home, None))
    return out


async def analyse(db: AsyncSession, site: SeoSite, actor: str, url: str | None = None) -> dict:
    """Reviews key landing pages (or one URL) and records what to fix."""
    reviewed, found, errors = 0, 0, []
    for page_url, kw in await _pages_to_review(db, site, url):
        try:
            page = await adapters.fetch_page(site, page_url)
        except SeoError as exc:
            errors.append(f"{page_url}: {exc.detail}")
            continue
        reviewed += 1
        signals = adapters.crawler.page_signals(page)
        buying = bool(kw and kw.purchase_intent >= 55)
        problems: dict[str, str] = {}
        if not signals["viewport"]:
            problems["not_mobile_friendly"] = "the page is not set up for phones (no viewport meta tag)"
        if not signals["cta_above_fold"]:
            problems["no_cta_above_fold"] = "no call to action is visible without scrolling"
        if signals["max_form_fields"] > 6:
            problems["form_too_long"] = f"the form asks for {signals['max_form_fields']} fields (more than 6)"
        if buying and not signals["trust"]:
            problems["no_trust_signals"] = "no reviews, testimonials or trust badges near the call to action"
        if buying and not signals["pricing"]:
            problems["no_pricing_info"] = "a buying-intent page shows no pricing or starting price"
        if kw:
            alignment = rules.jaccard(rules.stems(kw.term), rules.stems(f"{signals['title']} {signals['h1']}"))
            if alignment < 0.25:
                problems["intent_mismatch"] = f'the page does not clearly serve "{kw.term}" ({kw.intent} intent; alignment {round(alignment * 100)}/100)'
        slow = (await db.execute(select(SeoTechnicalIssue).where(SeoTechnicalIssue.site_id == site.id, SeoTechnicalIssue.url == page_url,
                                                                 SeoTechnicalIssue.code.in_(("lcp_poor", "lcp_slow")),
                                                                 SeoTechnicalIssue.status != "resolved"))).scalars().first()
        if slow:
            problems["slow_for_conversion"] = f"the page takes {slow.evidence.get('lcp_ms')} ms to show its main content"
        ctas = [c for c in signals["ctas"] if c]
        if ctas and max(rules.cta_strength(c) for c in ctas) < 50:
            weakest = min(ctas, key=rules.cta_strength)
            problems["weak_cta"] = f'the strongest call to action scores under 50 ("{weakest}" scores {rules.cta_strength(weakest)})'
        existing = {f.code: f for f in (await db.execute(select(SeoCroFinding).where(SeoCroFinding.site_id == site.id,
                                                                                     SeoCroFinding.page_url == page_url))).scalars()}
        for code, detail in problems.items():
            severity, change, rationale, test_idea = RECOMMEND[code]
            rec = {"change": change, "rationale": rationale, "test_idea": test_idea}
            if code == "weak_cta":
                rec["variants"] = rules.cta_variants(ctas[0] if ctas else "")
            f = existing.get(code)
            if f is None:
                db.add(SeoCroFinding(tenant_id=site.tenant_id, site_id=site.id, page_url=page_url, code=code, severity=severity,
                                     detail=detail, recommendation=rec, status="open"))
                found += 1
            elif f.status in ("open", "testing"):
                f.detail, f.recommendation = detail, rec
        for code, f in existing.items():
            if code not in problems and f.status == "open":
                f.status = "resolved"
    if not reviewed:
        raise SeoError(502, "no page could be reviewed: " + "; ".join(errors[:3]), "provider_error")
    await activity(db, site, "cro", f"Conversion review of {reviewed} page(s): {found} new finding(s)", actor)
    await db.commit()
    return {"pages_reviewed": reviewed, "new_findings": found, "errors": errors}


async def list_findings(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    order = {"critical": 0, "warning": 1, "info": 2}
    found = (await db.execute(select(SeoCroFinding).where(SeoCroFinding.site_id == site.id, SeoCroFinding.status.in_(("open", "testing"))))).scalars()
    return sorted((row(f) for f in found), key=lambda f: order.get(f["severity"], 3))


async def dismiss_finding(db: AsyncSession, tenant_id: str, finding_id: str) -> SeoCroFinding:
    f = await get_owned(db, SeoCroFinding, tenant_id, finding_id, "finding")
    f.status = "dismissed"
    await db.commit()
    return f


async def create_experiment(db: AsyncSession, tenant_id: str, site_id: str, body: dict, actor: str) -> SeoCroExperiment:
    site = await get_site(db, tenant_id, site_id)
    if not body.get("page_url") or not body.get("variant_a") or not body.get("variant_b"):
        raise invalid("page_url, variant_a and variant_b are required")
    texts = " ".join(str(v) for v in [*(body["variant_a"] or {}).values(), *(body["variant_b"] or {}).values()])
    findings = rules.guardrail(texts)
    exp = SeoCroExperiment(tenant_id=tenant_id, site_id=site.id, finding_id=body.get("finding_id"), page_url=body["page_url"][:1024],
                           element=body.get("element") or "cta", hypothesis=str(body.get("hypothesis") or ""), variant_a=body["variant_a"],
                           variant_b=body["variant_b"], guardrail_findings=findings, status="held" if findings else "running", created_by=actor)
    db.add(exp)
    if body.get("finding_id"):
        finding = await db.get(SeoCroFinding, parse_id(body["finding_id"], "finding"))
        if finding and finding.site_id == site.id:
            finding.status = "testing"
    await activity(db, site, "cro", f"Experiment {'held (guardrail)' if findings else 'started'} on {exp.page_url[:60]}", actor, "cro_experiment", exp.id)
    await db.commit()
    return exp


async def record_results(db: AsyncSession, tenant_id: str, exp_id: str, body: dict) -> SeoCroExperiment:
    exp = await get_owned(db, SeoCroExperiment, tenant_id, exp_id, "experiment")
    for field in ("a_visitors", "a_conversions", "b_visitors", "b_conversions"):
        if not isinstance(body.get(field), int) or isinstance(body.get(field), bool) or body[field] < 0:
            raise invalid(f"{field} must be a non-negative integer")
    if body["a_conversions"] > body["a_visitors"] or body["b_conversions"] > body["b_visitors"]:
        raise invalid("conversions cannot exceed visitors")
    exp.visitors_a, exp.conversions_a, exp.visitors_b, exp.conversions_b = (body["a_visitors"], body["a_conversions"],
                                                                            body["b_visitors"], body["b_conversions"])
    await db.commit()
    return exp


async def evaluate(db: AsyncSession, tenant_id: str, exp_id: str, actor: str) -> dict:
    exp = await get_owned(db, SeoCroExperiment, tenant_id, exp_id, "experiment")
    if exp.status != "running":
        return {"experiment": row(exp), "decision": exp.status}
    if min(exp.visitors_a, exp.visitors_b) < MIN_PER_ARM:
        return {"experiment": row(exp), "decision": "insufficient_data", "needed_per_arm": MIN_PER_ARM}
    p = rules.two_proportion_p_value(exp.visitors_a, exp.conversions_a, exp.visitors_b, exp.conversions_b)
    exp.p_value = round(p, 5)
    rate_a, rate_b = exp.conversions_a / exp.visitors_a, exp.conversions_b / exp.visitors_b
    decision = "keep_running"
    if p <= ALPHA:
        exp.winner, exp.status, exp.concluded_at, decision = ("A" if rate_a >= rate_b else "B"), "concluded", now(), "concluded"
    elif min(exp.visitors_a, exp.visitors_b) >= 4 * MIN_PER_ARM:
        exp.status, exp.concluded_at, decision = "inconclusive", now(), "inconclusive"
    lift = round((rate_b - rate_a) / rate_a * 100, 1) if rate_a else None
    site = await get_site(db, tenant_id, exp.site_id)
    await activity(db, site, "cro", f"Experiment on {exp.page_url[:60]}: {decision}" +
                   (f", winner {exp.winner} ({'+' if (lift or 0) >= 0 else ''}{lift}% vs control)" if exp.winner and lift is not None else ""),
                   actor, "cro_experiment", exp.id)
    await db.commit()
    if decision != "keep_running":
        await emit("seo.cro_experiment_evaluated", site, {"experiment_id": exp.id, "decision": decision})
    return {"experiment": row(exp), "decision": decision, "rate_a": round(rate_a, 4), "rate_b": round(rate_b, 4), "lift_pct": lift, "p_value": exp.p_value}


async def list_experiments(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(e) for e in (await db.execute(select(SeoCroExperiment).where(SeoCroExperiment.site_id == site.id)
                                              .order_by(SeoCroExperiment.created_at.desc()))).scalars()]


def cta_strength(text: str) -> dict:
    return {"text": text, "strength": rules.cta_strength(text or ""), "variants": rules.cta_variants(text or "")}


async def next_experiment(db: AsyncSession, site: SeoSite, actor: str) -> dict:
    """Starts a test for the highest-impact open finding that has none."""
    findings = await list_findings(db, site.tenant_id, str(site.id))
    candidate = next((f for f in findings if f["status"] == "open" and f["code"] in ("weak_cta", "no_cta_above_fold", "intent_mismatch")), None)
    if candidate is None:
        return {"started": None, "reason": "no open finding suits an A/B test"}
    current = (candidate["detail"].split('"')[1] if '"' in candidate["detail"] else "Learn more")
    variants = candidate["recommendation"].get("variants") or rules.cta_variants(current)
    exp = await create_experiment(db, site.tenant_id, str(site.id), {
        "page_url": candidate["page_url"], "finding_id": candidate["id"], "element": "cta",
        "hypothesis": candidate["recommendation"]["change"], "variant_a": {"cta": current}, "variant_b": {"cta": variants[0]}}, actor)
    if exp.status == "held":
        raise conflict("the experiment copy tripped the guardrail and is held for a person")
    return {"started": str(exp.id), "page_url": exp.page_url}
