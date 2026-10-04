import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.kafka_events import publish_event
from src.models import RiskAssessment, RiskBand, RiskPolicy

# Deterministic, auditable — not a magic number in a function body.
# Policy-type multipliers reflect that a liability policy's tail risk
# is genuinely different from an auto policy's, independent of coverage
# amount or claims history.
POLICY_TYPE_MULTIPLIER: dict[str, float] = {
    "auto": 1.0,
    "property": 1.2,
    "liability": 1.5,
    "life": 0.8,
}
DEFAULT_POLICY_TYPE_MULTIPLIER = 1.0

# Coverage amount contributes on a log-ish scale via a fixed divisor
# rather than linearly — a $10M policy is riskier than a $100K one,
# but not 100x riskier on that basis alone.
COVERAGE_SCORE_DIVISOR_USD = 200_000.0
COVERAGE_SCORE_CAP = 40.0

CLAIMS_SCORE_PER_CLAIM = 12.0
CLAIMS_SCORE_CAP = 36.0


def _score_policy(coverage_amount_usd: float, policy_type: str, claims_last_12mo: int) -> tuple[float, list[str]]:
    drivers: list[str] = []

    coverage_component = min(COVERAGE_SCORE_CAP, coverage_amount_usd / COVERAGE_SCORE_DIVISOR_USD)
    drivers.append(f"coverage ${coverage_amount_usd:,.0f} -> base component {coverage_component:.1f}")

    multiplier = POLICY_TYPE_MULTIPLIER.get(policy_type, DEFAULT_POLICY_TYPE_MULTIPLIER)
    if policy_type not in POLICY_TYPE_MULTIPLIER:
        drivers.append(f"unrecognized policy_type '{policy_type}' — default multiplier {DEFAULT_POLICY_TYPE_MULTIPLIER}x applied")
    else:
        drivers.append(f"policy_type '{policy_type}' multiplier {multiplier}x")

    claims_component = min(CLAIMS_SCORE_CAP, claims_last_12mo * CLAIMS_SCORE_PER_CLAIM)
    if claims_last_12mo:
        drivers.append(f"{claims_last_12mo} claim(s) in the last 12 months -> +{claims_component:.1f}")

    score = min(100.0, coverage_component * multiplier + claims_component)
    return score, drivers


def _band_for_score(score: float) -> RiskBand:
    settings = get_settings()
    if score >= settings.high_risk_score_threshold:
        return RiskBand.HIGH
    if score >= settings.elevated_risk_score_threshold:
        return RiskBand.ELEVATED
    return RiskBand.LOW


async def run_assessment(
    db: AsyncSession, tenant_id: str, policy_id: uuid.UUID, claims_last_12mo: int = 0
) -> RiskAssessment | None:
    """Tier 1 — anomaly flagging and policy simulation, per
    docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §4. Scores and
    records; never itself alters coverage, premium, or policy status."""
    result = await db.execute(select(RiskPolicy).where(RiskPolicy.tenant_id == tenant_id, RiskPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if policy is None:
        return None

    score, drivers = _score_policy(policy.coverage_amount_usd, policy.policy_type, claims_last_12mo)
    band = _band_for_score(score)

    assessment = RiskAssessment(
        tenant_id=tenant_id, policy_id=policy_id, risk_score=score, risk_band=band,
        drivers=drivers, reasoning="[deterministic] " + "; ".join(drivers) + f" -> score {score:.1f} ({band.value}).",
    )
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)

    await publish_event(
        "insurance_risk.assessment_completed",
        {"tenant_id": tenant_id, "policy_id": str(policy_id), "risk_score": score, "risk_band": band.value},
    )
    if band == RiskBand.HIGH:
        await publish_event(
            "insurance_risk.high_risk_flagged",
            {"tenant_id": tenant_id, "policy_id": str(policy_id), "risk_score": score},
        )

    return assessment


async def list_assessments(db: AsyncSession, tenant_id: str, policy_id: uuid.UUID) -> list[RiskAssessment]:
    result = await db.execute(
        select(RiskAssessment)
        .where(RiskAssessment.tenant_id == tenant_id, RiskAssessment.policy_id == policy_id)
        .order_by(RiskAssessment.created_at.desc())
    )
    return list(result.scalars().all())
