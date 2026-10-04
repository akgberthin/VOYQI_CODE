import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.kafka_events import publish_event
from src.models import ApplicationStatus, CreditDecision, CreditDecisionStatus, LoanApplication


class ApplicationNotFoundError(Exception):
    pass


class AccountableRoleRequiredError(Exception):
    """Raised for both an approval and a decline — an adverse credit
    action needs accountable documentation under fair-lending law just
    as much as an approval does."""


class AlreadyDecidedError(Exception):
    pass


async def request_decision(db: AsyncSession, tenant_id: str, application_id: uuid.UUID) -> CreditDecision:
    result = await db.execute(
        select(LoanApplication).where(LoanApplication.tenant_id == tenant_id, LoanApplication.id == application_id)
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise ApplicationNotFoundError(f"application {application_id} not found")

    decision = CreditDecision(tenant_id=tenant_id, application_id=application_id)
    db.add(decision)
    application.status = ApplicationStatus.PENDING_DECISION
    await db.commit()
    await db.refresh(decision)
    return decision


async def get_decision(db: AsyncSession, tenant_id: str, decision_id: uuid.UUID) -> CreditDecision | None:
    result = await db.execute(
        select(CreditDecision).where(CreditDecision.tenant_id == tenant_id, CreditDecision.id == decision_id)
    )
    return result.scalar_one_or_none()


async def list_decisions(
    db: AsyncSession, tenant_id: str, status: CreditDecisionStatus | None = None
) -> list[CreditDecision]:
    stmt = select(CreditDecision).where(CreditDecision.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(CreditDecision.status == status)
    result = await db.execute(stmt.order_by(CreditDecision.created_at.desc()))
    return list(result.scalars().all())


async def decide(
    db: AsyncSession, tenant_id: str, decision_id: uuid.UUID, approve: bool,
    accountable_role: str | None, actor: str, reasoning: str = "",
) -> CreditDecision | None:
    """credit.decision — the platform's other hard, unconditional tier-3
    gate (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4). No risk
    score, no threshold, no AI code path reaches this function without
    a human supplying a named accountable_role — for a decline exactly
    as much as an approval."""
    decision = await get_decision(db, tenant_id, decision_id)
    if decision is None:
        return None
    if decision.status != CreditDecisionStatus.PENDING:
        raise AlreadyDecidedError(f"decision already {decision.status.value}")
    if not accountable_role:
        raise AccountableRoleRequiredError("accountable_role is required to decide a credit application")

    decision.status = CreditDecisionStatus.APPROVED if approve else CreditDecisionStatus.DECLINED
    decision.accountable_role = accountable_role
    decision.decided_by = actor
    decision.decision_reasoning = reasoning
    decision.decided_at = datetime.now(timezone.utc)

    app_result = await db.execute(select(LoanApplication).where(LoanApplication.id == decision.application_id))
    application = app_result.scalar_one()
    application.status = ApplicationStatus.APPROVED if approve else ApplicationStatus.DECLINED

    await db.commit()
    await db.refresh(decision)

    await publish_event(
        "originations.credit_decision_made",
        {
            "tenant_id": tenant_id, "decision_id": str(decision.id), "application_id": str(decision.application_id),
            "approved": approve, "accountable_role": accountable_role,
        },
    )
    return decision
