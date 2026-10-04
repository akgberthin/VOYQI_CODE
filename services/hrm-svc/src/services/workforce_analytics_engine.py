"""
HRM-005 Workforce Analytics & Retention Intelligence.

Headcount planning, compensation benchmarking, and pay-equity analytics
are entirely deterministic aggregation over existing HRM-001/002/004
data — no AI anywhere in this arithmetic. Pay-equity analytics is
scoped conservatively, the same principle as ORG-005's disparate-impact
scoring: aggregate stats grouped only by business variables (department,
tenure bucket) — never by anything that could proxy for a protected
characteristic.

record_attrition_risk is the only function that ever writes an
AttritionRiskScore — always from the Talent Agent's evaluation.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    AttritionRiskScore,
    CompensationRecord,
    EmployeeProfile,
    EmploymentStatus,
    Position,
)

# ── Attrition risk (Talent Agent output) ────────────────────────────────

async def record_attrition_risk(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID, risk_score: float, reasoning: str) -> AttritionRiskScore:
    score = AttritionRiskScore(tenant_id=tenant_id, employee_id=employee_id, risk_score=risk_score, reasoning=reasoning)
    db.add(score)
    await db.commit()
    await db.refresh(score)
    return score


async def get_latest_attrition_risk(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> AttritionRiskScore | None:
    result = await db.execute(
        select(AttritionRiskScore).where(AttritionRiskScore.tenant_id == tenant_id, AttritionRiskScore.employee_id == employee_id)
        .order_by(AttritionRiskScore.computed_at.desc())
    )
    return result.scalars().first()


async def list_high_attrition_risk_employees(db: AsyncSession, tenant_id: str, threshold: float = 0.5) -> list[AttritionRiskScore]:
    """Every active employee's most recent score above the threshold —
    plain filtering, no AI, the same watchlist pattern used for fleet
    safety scores and battery health."""
    result = await db.execute(select(AttritionRiskScore).where(AttritionRiskScore.tenant_id == tenant_id))
    all_scores = list(result.scalars().all())
    latest_by_employee: dict[uuid.UUID, AttritionRiskScore] = {}
    for s in all_scores:
        existing = latest_by_employee.get(s.employee_id)
        if existing is None or s.computed_at > existing.computed_at:
            latest_by_employee[s.employee_id] = s
    return [s for s in latest_by_employee.values() if s.risk_score >= threshold]


# ── Attrition risk factor gathering (feeds the Talent Agent) ───────────

async def get_attrition_risk_factors(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> dict:
    """Gathers the real, auditable inputs the Talent Agent's heuristic
    uses — tenure, compensation-vs-band position, months since last
    comp change, and goal completion rate (from HRM-004). No AI here;
    this is just assembling facts for the agent to score."""
    from src.services import performance_engine

    emp_result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.id == employee_id, EmployeeProfile.tenant_id == tenant_id))
    employee = emp_result.scalar_one_or_none()
    if employee is None:
        return {}

    now = datetime.now(timezone.utc)
    hire_date = employee.hire_date if employee.hire_date.tzinfo else employee.hire_date.replace(tzinfo=timezone.utc)
    tenure_months = max(0, int((now - hire_date).days / 30))

    comp_result = await db.execute(
        select(CompensationRecord).where(CompensationRecord.tenant_id == tenant_id, CompensationRecord.employee_id == employee_id)
        .order_by(CompensationRecord.effective_date.desc())
    )
    comp_records = list(comp_result.scalars().all())
    comp_band_position = None
    months_since_last_comp_change = None
    if comp_records:
        latest_comp = comp_records[0]
        effective_date = latest_comp.effective_date if latest_comp.effective_date.tzinfo else latest_comp.effective_date.replace(tzinfo=timezone.utc)
        months_since_last_comp_change = max(0, int((now - effective_date).days / 30))

        position_result = await db.execute(
            select(Position).where(Position.tenant_id == tenant_id, Position.title == employee.job_title, Position.department == employee.department)
        )
        position = position_result.scalars().first()
        if position and position.salary_band_max > position.salary_band_min:
            comp_band_position = round(
                (latest_comp.annual_salary_usd - position.salary_band_min) / (position.salary_band_max - position.salary_band_min), 2
            )

    goal_completion_rate = await performance_engine.get_goal_completion_rate(db, tenant_id, employee_id)

    return {
        "tenure_months": tenure_months,
        "comp_band_position": comp_band_position,
        "months_since_last_comp_change": months_since_last_comp_change,
        "goal_completion_rate": goal_completion_rate,
    }


# ── Headcount planning (deterministic aggregation) ─────────────────────

async def get_headcount_by_department(db: AsyncSession, tenant_id: str) -> list[dict]:
    result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.tenant_id == tenant_id, EmployeeProfile.employment_status == EmploymentStatus.ACTIVE))
    employees = list(result.scalars().all())
    by_dept: dict[str, int] = {}
    for e in employees:
        dept = e.department or "(unassigned)"
        by_dept[dept] = by_dept.get(dept, 0) + 1
    return [{"department": k, "headcount": v} for k, v in by_dept.items()]


async def get_headcount_by_entity(db: AsyncSession, tenant_id: str) -> list[dict]:
    result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.tenant_id == tenant_id, EmployeeProfile.employment_status == EmploymentStatus.ACTIVE))
    employees = list(result.scalars().all())
    by_entity: dict[str, int] = {}
    for e in employees:
        entity = e.entity_ref or "(unassigned)"
        by_entity[entity] = by_entity.get(entity, 0) + 1
    return [{"entity_ref": k, "headcount": v} for k, v in by_entity.items()]


# ── Compensation benchmarking (deterministic, vs. Position salary band) ─

async def get_compensation_benchmark(db: AsyncSession, tenant_id: str, employee_id: uuid.UUID) -> dict:
    emp_result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.id == employee_id, EmployeeProfile.tenant_id == tenant_id))
    employee = emp_result.scalar_one_or_none()
    if employee is None:
        return {"error": "employee not found"}

    comp_result = await db.execute(
        select(CompensationRecord).where(CompensationRecord.tenant_id == tenant_id, CompensationRecord.employee_id == employee_id)
        .order_by(CompensationRecord.effective_date.desc())
    )
    latest_comp = comp_result.scalars().first()
    if latest_comp is None:
        return {"employee_id": str(employee_id), "current_salary_usd": None, "band_min_usd": None, "band_max_usd": None, "band_position_pct": None}

    position_result = await db.execute(
        select(Position).where(Position.tenant_id == tenant_id, Position.title == employee.job_title, Position.department == employee.department)
    )
    position = position_result.scalars().first()
    if position is None or position.salary_band_max <= position.salary_band_min:
        return {"employee_id": str(employee_id), "current_salary_usd": latest_comp.annual_salary_usd, "band_min_usd": None, "band_max_usd": None, "band_position_pct": None}

    band_position_pct = round(((latest_comp.annual_salary_usd - position.salary_band_min) / (position.salary_band_max - position.salary_band_min)) * 100, 1)
    return {
        "employee_id": str(employee_id),
        "current_salary_usd": latest_comp.annual_salary_usd,
        "band_min_usd": position.salary_band_min,
        "band_max_usd": position.salary_band_max,
        "band_position_pct": band_position_pct,
    }


# ── Pay-equity analytics (aggregate stats, business variables only) ───

def _tenure_bucket(hire_date: datetime, now: datetime) -> str:
    months = max(0, int((now - hire_date).days / 30))
    if months < 12:
        return "0-1 years"
    if months < 36:
        return "1-3 years"
    if months < 60:
        return "3-5 years"
    return "5+ years"


async def get_pay_equity_snapshot(db: AsyncSession, tenant_id: str) -> list[dict]:
    """Aggregate compensation stats grouped by department and tenure
    bucket only — business variables, never anything that could proxy
    for a protected characteristic. Mirrors ORG-005's disparate-impact
    scoping exactly: this reports aggregate statistics for HR to review,
    it never flags or names an individual employee."""
    emp_result = await db.execute(select(EmployeeProfile).where(EmployeeProfile.tenant_id == tenant_id, EmployeeProfile.employment_status == EmploymentStatus.ACTIVE))
    employees = list(emp_result.scalars().all())
    now = datetime.now(timezone.utc)

    groups: dict[tuple[str, str], list[float]] = {}
    for e in employees:
        comp_result = await db.execute(
            select(CompensationRecord).where(CompensationRecord.tenant_id == tenant_id, CompensationRecord.employee_id == e.id)
            .order_by(CompensationRecord.effective_date.desc())
        )
        latest_comp = comp_result.scalars().first()
        if latest_comp is None:
            continue
        hire_date = e.hire_date if e.hire_date.tzinfo else e.hire_date.replace(tzinfo=timezone.utc)
        key = (e.department or "(unassigned)", _tenure_bucket(hire_date, now))
        groups.setdefault(key, []).append(latest_comp.annual_salary_usd)

    snapshot = []
    for (department, tenure_bucket), salaries in groups.items():
        snapshot.append({
            "department": department,
            "tenure_bucket": tenure_bucket,
            "employee_count": len(salaries),
            "avg_salary_usd": round(sum(salaries) / len(salaries), 2),
            "min_salary_usd": round(min(salaries), 2),
            "max_salary_usd": round(max(salaries), 2),
        })
    return snapshot
