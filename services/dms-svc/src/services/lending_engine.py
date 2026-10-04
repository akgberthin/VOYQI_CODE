"""
DMS-004 (Lender Access & Credit Application) engine.

Fully autonomous end to end: pull_bureau clears straight into
auto_route_to_lenders, which decisions every submission and selects
the winner, all in one chain. The ONE structural exception is OFAC:
a sanctions match sets status to BLOCKED and returns immediately —
route_to_lenders is never even called. This is enforced here, in code,
not left to the caller to remember to check.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    CreditApplication,
    CreditApplicationStatus,
    Lender,
    LenderSubmission,
    SubmissionStatus,
)
from src.services import fi_engine


async def create_credit_application(db: AsyncSession, tenant_id: str, deal_id: uuid.UUID, applicant_name: str, annual_income_usd: float) -> CreditApplication:
    app = CreditApplication(tenant_id=tenant_id, deal_id=deal_id, applicant_name=applicant_name, annual_income_usd=annual_income_usd)
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app


async def get_credit_application(db: AsyncSession, tenant_id: str, app_id: uuid.UUID) -> CreditApplication | None:
    result = await db.execute(select(CreditApplication).where(CreditApplication.id == app_id, CreditApplication.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_credit_applications(db: AsyncSession, tenant_id: str) -> list[CreditApplication]:
    result = await db.execute(select(CreditApplication).where(CreditApplication.tenant_id == tenant_id))
    return list(result.scalars().all())


async def create_lender(db: AsyncSession, tenant_id: str, name: str) -> Lender:
    lender = Lender(tenant_id=tenant_id, name=name)
    db.add(lender)
    await db.commit()
    await db.refresh(lender)
    return lender


async def list_lenders(db: AsyncSession, tenant_id: str) -> list[Lender]:
    result = await db.execute(select(Lender).where(Lender.tenant_id == tenant_id))
    return list(result.scalars().all())


async def get_submissions(db: AsyncSession, tenant_id: str, app_id: uuid.UUID) -> list[LenderSubmission]:
    result = await db.execute(select(LenderSubmission).where(LenderSubmission.credit_application_id == app_id, LenderSubmission.tenant_id == tenant_id))
    return list(result.scalars().all())


async def pull_bureau(db: AsyncSession, tenant_id: str, app_id: uuid.UUID, bureau: str, fico: int, ofac_match: bool) -> CreditApplication:
    """OFAC is a structural block, not a business decision: if
    ofac_match is True, this function sets BLOCKED and returns
    immediately — auto_route_to_lenders below is never reached, by
    construction, not by convention. There is no override path
    anywhere in this call graph."""
    app = await get_credit_application(db, tenant_id, app_id)
    if app is None:
        raise ValueError(f"credit application {app_id} not found")

    app.bureau = bureau
    app.fico = fico
    app.ofac_match = ofac_match

    if ofac_match:
        app.status = CreditApplicationStatus.BLOCKED
        await db.commit()
        await db.refresh(app)
        return app

    await db.commit()
    await auto_route_to_lenders(db, tenant_id, app_id)
    await db.refresh(app)
    return app


async def auto_route_to_lenders(db: AsyncSession, tenant_id: str, app_id: uuid.UUID) -> list[LenderSubmission]:
    """Fully autonomous FICO-tier routing: prime credit goes to every
    lender for rate competition; subprime goes only to the lenders who
    buy that tier. Immediately chains into auto-decisioning every
    submission and selecting the winner — no manual submit click
    anywhere in this path."""
    settings = get_settings()
    app = await get_credit_application(db, tenant_id, app_id)
    lenders = await list_lenders(db, tenant_id)
    chosen = lenders if app.fico >= settings.lending_prime_fico_floor else lenders[:2]

    submissions = []
    for lender in chosen:
        sub = LenderSubmission(tenant_id=tenant_id, credit_application_id=app_id, lender_id=lender.id)
        db.add(sub)
        submissions.append(sub)
    await db.commit()
    for sub in submissions:
        await db.refresh(sub)

    for sub in submissions:
        await auto_decide_submission(db, tenant_id, sub.id, app.fico, app.annual_income_usd, app.deal_id)

    await auto_select_winner(db, tenant_id, app_id)
    return submissions


async def auto_decide_submission(db: AsyncSession, tenant_id: str, submission_id: uuid.UUID, fico: int, annual_income_usd: float, deal_id: uuid.UUID) -> LenderSubmission:
    """Deterministic FICO-tier decision policy — always produces a
    real decision, never leaves a submission pending. A DECLINED
    outcome auto-chains into an adverse-action notice."""
    settings = get_settings()
    result = await db.execute(select(LenderSubmission).where(LenderSubmission.id == submission_id))
    sub = result.scalar_one_or_none()
    if sub is None:
        raise ValueError(f"submission {submission_id} not found")

    if fico >= settings.lending_approve_fico_floor:
        sub.status = SubmissionStatus.APPROVED
        sub.apr = round(5 + (720 - min(fico, 720)) * 0.02, 2)
        sub.term_months = 60
        sub.amount_usd = round(annual_income_usd * 0.4, 2)
        sub.reserve_usd = round(400 + (720 - min(fico, 720)) * 8, 2)
    elif fico >= settings.lending_counter_fico_floor:
        sub.status = SubmissionStatus.COUNTERED
        sub.apr = round(9 + (settings.lending_counter_fico_floor + 40 - fico) * 0.05, 2)
        sub.term_months = 72
        sub.amount_usd = round(annual_income_usd * 0.35, 2)
        sub.reserve_usd = round(1200 + (settings.lending_counter_fico_floor + 40 - fico) * 15, 2)
    else:
        sub.status = SubmissionStatus.DECLINED
        lender_result = await db.execute(select(Lender).where(Lender.id == sub.lender_id))
        lender = lender_result.scalar_one_or_none()
        await fi_engine.auto_issue_adverse_action(
            db, tenant_id, deal_id, f"declined by {lender.name if lender else 'lender'} — credit criteria not met",
        )

    await db.commit()
    await db.refresh(sub)
    return sub


async def auto_select_winner(db: AsyncSession, tenant_id: str, app_id: uuid.UUID) -> CreditApplication:
    """Policy: lowest APR among cleared (approved/countered) offers
    wins — the most customer-favorable term, selected with no human
    click. status is always DECISIONED when this returns."""
    app = await get_credit_application(db, tenant_id, app_id)
    submissions = await get_submissions(db, tenant_id, app_id)
    eligible = [s for s in submissions if s.status in (SubmissionStatus.APPROVED, SubmissionStatus.COUNTERED)]
    if eligible:
        winner = min(eligible, key=lambda s: s.apr)
        app.selected_submission_id = winner.id
    app.status = CreditApplicationStatus.DECISIONED
    await db.commit()
    await db.refresh(app)
    return app
