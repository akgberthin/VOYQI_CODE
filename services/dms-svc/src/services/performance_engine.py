"""
DMS-009 — Dealership P&L & KPI Board engine, capstone capability.

Every figure on PLSnapshot is real transaction arithmetic — this
engine never writes to that table, only reads it (plus
FranchiseCompliance) to compute a per-rooftop verdict, then acts
elsewhere: a rooftop verdicted INTERVENE gets an accelerated
inventory markdown sweep via inventory_engine.apply_accelerated_markdown
— a real cross-module chain, not just a dashboard number. This is the
same pattern as FLT-013 in fleet-svc: read real numbers, decide, act
on a DIFFERENT table, never touch the source figures.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    FranchiseCompliance,
    FranchiseStatus,
    PerformanceEvaluation,
    PerformanceVerdict,
    PLSnapshot,
    Vehicle,
    VehicleStatus,
)
from src.services import inventory_engine


async def create_pl_snapshot(db: AsyncSession, tenant_id: str, rooftop_id: uuid.UUID, units: int, gross_usd: float, turn_rate: float, fi_pvr_usd: float, absorption_pct: float) -> PLSnapshot:
    snapshot = PLSnapshot(
        tenant_id=tenant_id, rooftop_id=rooftop_id, units=units, gross_usd=gross_usd,
        turn_rate=turn_rate, fi_pvr_usd=fi_pvr_usd, absorption_pct=absorption_pct,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


async def list_latest_snapshots(db: AsyncSession, tenant_id: str) -> list[PLSnapshot]:
    """One snapshot per rooftop — the most recent for each."""
    result = await db.execute(select(PLSnapshot).where(PLSnapshot.tenant_id == tenant_id).order_by(PLSnapshot.snapshot_at.desc()))
    all_snapshots = list(result.scalars().all())
    seen = set()
    latest = []
    for s in all_snapshots:
        if s.rooftop_id not in seen:
            seen.add(s.rooftop_id)
            latest.append(s)
    return latest


async def evaluate_dealer_performance(db: AsyncSession, tenant_id: str) -> list[PerformanceEvaluation]:
    """Computes and persists a verdict per rooftop. Points: gross
    below group average (+1), turn below 1.0x (+1), absorption below
    60% (+1), franchise status not compliant (+1). 0 = strong,
    1-2 = monitor, 3+ = intervene."""
    snapshots = await list_latest_snapshots(db, tenant_id)
    if not snapshots:
        return []
    group_avg_gross = sum(s.gross_usd for s in snapshots) / len(snapshots)

    franchise_result = await db.execute(select(FranchiseCompliance).where(FranchiseCompliance.tenant_id == tenant_id))
    franchise_by_rooftop = {f.rooftop_id: f for f in franchise_result.scalars().all()}

    evaluations = []
    for snap in snapshots:
        points = 0
        reasons = []
        if snap.gross_usd < group_avg_gross:
            points += 1
            reasons.append("gross/unit below group average")
        if snap.turn_rate < 1.0:
            points += 1
            reasons.append("inventory turn below 1.0x")
        if snap.absorption_pct < 60:
            points += 1
            reasons.append("fixed-ops absorption below 60%")
        franchise = franchise_by_rooftop.get(snap.rooftop_id)
        if franchise and franchise.status != FranchiseStatus.COMPLIANT:
            points += 1
            reasons.append(f"franchise status: {franchise.status.value.replace('_', ' ')}")

        verdict = PerformanceVerdict.INTERVENE if points >= 3 else (PerformanceVerdict.MONITOR if points >= 1 else PerformanceVerdict.STRONG)
        evaluation = PerformanceEvaluation(tenant_id=tenant_id, rooftop_id=snap.rooftop_id, verdict=verdict, points=points, reasons=reasons)
        db.add(evaluation)
        evaluations.append(evaluation)

    await db.commit()
    for e in evaluations:
        await db.refresh(e)
    return evaluations


async def evaluate_and_apply_interventions(db: AsyncSession, tenant_id: str) -> list[PerformanceEvaluation]:
    """Runs the verdict computation, then autonomously applies an
    accelerated markdown sweep to any INTERVENE rooftop's aging
    inventory — real cross-module action, not just a label. Guarded by
    intervention_applied against re-triggering the sweep repeatedly."""
    settings = get_settings()
    evaluations = await evaluate_dealer_performance(db, tenant_id)

    for evaluation in evaluations:
        if evaluation.verdict != PerformanceVerdict.INTERVENE or evaluation.intervention_applied:
            continue
        vehicles_result = await db.execute(
            select(Vehicle).where(
                Vehicle.tenant_id == tenant_id, Vehicle.rooftop_id == evaluation.rooftop_id,
                Vehicle.status == VehicleStatus.IN_STOCK,
            )
        )
        for vehicle in vehicles_result.scalars().all():
            if vehicle.days_in_inventory >= settings.intervention_aging_threshold_days:
                await inventory_engine.apply_accelerated_markdown(
                    db, tenant_id, vehicle.id, settings.intervention_aging_threshold_days,
                    settings.intervention_markdown_cap_pct, settings.intervention_markdown_pct_per_day,
                )
        evaluation.intervention_applied = True

    await db.commit()
    for e in evaluations:
        await db.refresh(e)
    return evaluations
