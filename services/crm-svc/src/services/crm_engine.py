"""CRM engine. Deal stage transitions are validated, not free-form:
closed_won and closed_lost are terminal — once a deal is closed, moving
it to any other stage (including back to an open stage, or from one
closed state to the other) requires the explicit reopen_deal() call,
never a plain advance_deal_stage(), so a closed deal's history can't be
silently rewritten by a routine stage update.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CLOSED_STAGES,
    OPEN_STAGES,
    Activity,
    Deal,
    DealStage,
    Lead,
    LeadStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeadNotFoundError(Exception):
    pass


class DealNotFoundError(Exception):
    pass


class LeadAlreadyConvertedError(Exception):
    pass


class DealClosedError(Exception):
    pass


class InvalidStageTransitionError(Exception):
    pass


async def capture_lead(db: AsyncSession, *, tenant_id: str, first_name: str, last_name: str, email: str, phone: str | None = None, source: str = "unknown", external_ref_id: str | None = None) -> Lead:
    lead = Lead(
        tenant_id=tenant_id, first_name=first_name, last_name=last_name, email=email.lower().strip(), phone=phone, source=source,
        external_ref_id=external_ref_id or None,
    )
    db.add(lead)
    await db.flush()
    return lead


async def find_duplicate_leads(db: AsyncSession, *, tenant_id: str, email: str = "", phone: str = "") -> list[Lead]:
    """Exact email or phone matches only — the agent applies judgment on top."""
    conditions = []
    if email.strip():
        conditions.append(Lead.email == email.lower().strip())
    if phone.strip():
        conditions.append(Lead.phone == phone.strip())
    if not conditions:
        return []
    result = await db.execute(select(Lead).where(Lead.tenant_id == tenant_id, or_(*conditions)))
    return list(result.scalars().all())


async def score_lead(db: AsyncSession, *, tenant_id: str, lead_id: uuid.UUID, score: float, reasoning: str, routed_to_team: str) -> Lead:
    lead = await get_lead(db, tenant_id=tenant_id, lead_id=lead_id)
    lead.score = score
    lead.score_reasoning = reasoning
    lead.routed_to_team = routed_to_team
    await db.flush()
    return lead


async def get_lead(db: AsyncSession, *, tenant_id: str, lead_id: uuid.UUID) -> Lead:
    result = await db.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    lead = result.scalar_one_or_none()
    if lead is None:
        raise LeadNotFoundError(f"lead {lead_id} not found")
    return lead


async def qualify_lead(db: AsyncSession, *, tenant_id: str, lead_id: uuid.UUID, qualified: bool, reason: str | None = None, assigned_to: uuid.UUID | None = None) -> Lead:
    lead = await get_lead(db, tenant_id=tenant_id, lead_id=lead_id)
    if lead.status == LeadStatus.CONVERTED.value:
        raise LeadAlreadyConvertedError(f"lead {lead_id} was already converted, its status can no longer change")
    lead.status = LeadStatus.QUALIFIED.value if qualified else LeadStatus.DISQUALIFIED.value
    lead.disqualify_reason = None if qualified else reason
    if assigned_to is not None:
        lead.assigned_to = assigned_to
    await db.flush()
    return lead


async def convert_lead_to_deal(
    db: AsyncSession, *, tenant_id: str, lead_id: uuid.UUID, deal_name: str, value_cents: int, owner_id: uuid.UUID,
    mobility_class: str | None = None, expected_close_date: datetime | None = None,
) -> Deal:
    lead = await get_lead(db, tenant_id=tenant_id, lead_id=lead_id)
    if lead.status == LeadStatus.CONVERTED.value:
        raise LeadAlreadyConvertedError(f"lead {lead_id} was already converted")

    deal = Deal(
        tenant_id=tenant_id, lead_id=lead_id, name=deal_name, value_cents=value_cents, mobility_class=mobility_class,
        owner_id=owner_id, expected_close_date=expected_close_date,
    )
    db.add(deal)
    lead.status = LeadStatus.CONVERTED.value
    await db.flush()
    return deal


async def create_deal(
    db: AsyncSession, *, tenant_id: str, name: str, value_cents: int, owner_id: uuid.UUID,
    mobility_class: str | None = None, expected_close_date: datetime | None = None,
) -> Deal:
    """A deal created directly, without going through a lead — a
    corporate account approaching sales directly, for instance."""
    deal = Deal(tenant_id=tenant_id, name=name, value_cents=value_cents, mobility_class=mobility_class, owner_id=owner_id, expected_close_date=expected_close_date)
    db.add(deal)
    await db.flush()
    return deal


async def get_deal(db: AsyncSession, *, tenant_id: str, deal_id: uuid.UUID) -> Deal:
    result = await db.execute(select(Deal).where(Deal.id == deal_id, Deal.tenant_id == tenant_id))
    deal = result.scalar_one_or_none()
    if deal is None:
        raise DealNotFoundError(f"deal {deal_id} not found")
    return deal


async def advance_deal_stage(db: AsyncSession, *, tenant_id: str, deal_id: uuid.UUID, new_stage: str) -> Deal:
    deal = await get_deal(db, tenant_id=tenant_id, deal_id=deal_id)

    if deal.stage in CLOSED_STAGES:
        raise DealClosedError(f"deal {deal_id} is already {deal.stage} — use reopen_deal() to change a closed deal's stage")

    if new_stage not in OPEN_STAGES and new_stage not in CLOSED_STAGES:
        raise InvalidStageTransitionError(f"'{new_stage}' is not a recognized deal stage")

    if new_stage in CLOSED_STAGES:
        deal.closed_at = utcnow()

    deal.stage = new_stage
    await db.flush()
    return deal


async def close_deal(db: AsyncSession, *, tenant_id: str, deal_id: uuid.UUID, won: bool, reason: str | None = None) -> Deal:
    deal = await get_deal(db, tenant_id=tenant_id, deal_id=deal_id)
    if deal.stage in CLOSED_STAGES:
        raise DealClosedError(f"deal {deal_id} is already {deal.stage}")

    deal.stage = DealStage.CLOSED_WON.value if won else DealStage.CLOSED_LOST.value
    deal.closed_at = utcnow()
    deal.close_reason = reason
    await db.flush()
    return deal


async def reopen_deal(db: AsyncSession, *, tenant_id: str, deal_id: uuid.UUID, new_stage: str = DealStage.PROSPECTING.value) -> Deal:
    deal = await get_deal(db, tenant_id=tenant_id, deal_id=deal_id)
    if deal.stage not in CLOSED_STAGES:
        raise InvalidStageTransitionError(f"deal {deal_id} is not closed, nothing to reopen")
    if new_stage not in OPEN_STAGES:
        raise InvalidStageTransitionError(f"can only reopen into an open stage, got '{new_stage}'")

    deal.stage = new_stage
    deal.closed_at = None
    deal.close_reason = None
    await db.flush()
    return deal


async def log_activity(
    db: AsyncSession, *, tenant_id: str, activity_type: str, subject: str, created_by: uuid.UUID,
    deal_id: uuid.UUID | None = None, lead_id: uuid.UUID | None = None, notes: str | None = None,
) -> Activity:
    if deal_id is None and lead_id is None:
        raise ValueError("an activity must be attached to a deal, a lead, or both")
    activity = Activity(tenant_id=tenant_id, deal_id=deal_id, lead_id=lead_id, activity_type=activity_type, subject=subject, notes=notes, created_by=created_by)
    db.add(activity)
    await db.flush()
    return activity


async def get_pipeline_summary(db: AsyncSession, *, tenant_id: str, owner_id: uuid.UUID | None = None) -> dict:
    """Real aggregation, not a placeholder — deal count and total value
    per stage, the numbers an actual sales pipeline dashboard needs."""
    query = select(Deal.stage, func.count(Deal.id), func.sum(Deal.value_cents)).where(Deal.tenant_id == tenant_id)
    if owner_id is not None:
        query = query.where(Deal.owner_id == owner_id)
    query = query.group_by(Deal.stage)

    result = await db.execute(query)
    by_stage = {stage: {"count": count, "total_value_cents": total or 0} for stage, count, total in result.all()}

    all_stages = OPEN_STAGES + CLOSED_STAGES
    return {stage: by_stage.get(stage, {"count": 0, "total_value_cents": 0}) for stage in all_stages}
