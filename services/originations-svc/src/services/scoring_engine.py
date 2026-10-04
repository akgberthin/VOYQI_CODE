import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.kafka_events import publish_event
from src.models import ApplicationScore, ApplicationStatus, LeadBand, LoanApplication

# FICO ranges 300-850; the divisor makes an 850 FICO worth the full 50
# points ((850-300)/11 = 50), a 300 FICO worth zero. Named here, not
# buried in the function body.
FICO_FLOOR = 300
FICO_COMPONENT_DIVISOR = 11.0
FICO_COMPONENT_CAP = 50.0

RATIO_COMPONENT_MULTIPLIER = 25.0
RATIO_COMPONENT_CAP = 50.0


def _score(fico_score: int, annual_income_usd: float, requested_amount_usd: float) -> tuple[float, list[str]]:
    drivers: list[str] = []

    fico_component = min(FICO_COMPONENT_CAP, max(0.0, (fico_score - FICO_FLOOR) / FICO_COMPONENT_DIVISOR))
    drivers.append(f"FICO {fico_score} -> {fico_component:.1f} pts")

    ratio = annual_income_usd / requested_amount_usd if requested_amount_usd else 0.0
    ratio_component = min(RATIO_COMPONENT_CAP, ratio * RATIO_COMPONENT_MULTIPLIER)
    drivers.append(f"income/requested ratio {ratio:.2f} -> {ratio_component:.1f} pts")

    return fico_component + ratio_component, drivers


def _band_for_score(score: float) -> LeadBand:
    settings = get_settings()
    if score >= settings.hot_score_threshold:
        return LeadBand.HOT
    if score >= settings.warm_score_threshold:
        return LeadBand.WARM
    return LeadBand.COLD


async def score_application(db: AsyncSession, tenant_id: str, application_id: uuid.UUID) -> ApplicationScore | None:
    """lead.score_and_route — tier 1, per docs/AUTONOMY_TIERING_
    ENGINEER_INSTRUCTIONS.md §4. Deterministic and auto-executes; never
    itself approves or declines the application."""
    result = await db.execute(
        select(LoanApplication).where(LoanApplication.tenant_id == tenant_id, LoanApplication.id == application_id)
    )
    application = result.scalar_one_or_none()
    if application is None:
        return None

    score, drivers = _score(application.fico_score, application.annual_income_usd, application.requested_amount_usd)
    band = _band_for_score(score)

    record = ApplicationScore(
        tenant_id=tenant_id, application_id=application_id, score=score, band=band, drivers=drivers,
        reasoning="[deterministic] " + "; ".join(drivers) + f" -> score {score:.1f} ({band.value}).",
    )
    db.add(record)
    application.status = ApplicationStatus.SCORED
    await db.commit()
    await db.refresh(record)

    await publish_event(
        "originations.application_scored",
        {"tenant_id": tenant_id, "application_id": str(application_id), "score": score, "band": band.value},
    )
    return record


async def list_scores(db: AsyncSession, tenant_id: str, application_id: uuid.UUID) -> list[ApplicationScore]:
    result = await db.execute(
        select(ApplicationScore)
        .where(ApplicationScore.tenant_id == tenant_id, ApplicationScore.application_id == application_id)
        .order_by(ApplicationScore.created_at.desc())
    )
    return list(result.scalars().all())
