"""
FLT-011 Fuel & Energy Cost Management — cost-per-mile and efficiency
are entirely deterministic, plain arithmetic over logged fill-ups/
charges. No AI touches those calculations; they remain hand-verifiable
with a calculator, the same principle as ORG-004's e-contracting and
HRM-002's payroll math. evaluate_fuel_anomaly in the Fleet Intelligence
Agent adds a separate, genuine judgment-call signal on top — flagging
entries that suggest fraud, a data entry error, or a maintenance-
driven efficiency drop — without ever altering the logged entry or the
deterministic math itself.
"""
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import EnergyType, FuelAnomalyAssessment, FuelAnomalyLevel, FuelLogEntry

if TYPE_CHECKING:
    from src.models import MaintenanceAlert


async def log_fuel_entry(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, energy_type: EnergyType, quantity: float, cost_usd: float, odometer_miles: int) -> FuelLogEntry:
    entry = FuelLogEntry(tenant_id=tenant_id, vehicle_id=vehicle_id, energy_type=energy_type, quantity=quantity, cost_usd=cost_usd, odometer_miles=odometer_miles)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_fuel_entries(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> list[FuelLogEntry]:
    result = await db.execute(
        select(FuelLogEntry).where(FuelLogEntry.tenant_id == tenant_id, FuelLogEntry.vehicle_id == vehicle_id).order_by(FuelLogEntry.odometer_miles)
    )
    return list(result.scalars().all())


def compute_cost_per_mile(entries: list[FuelLogEntry]) -> float | None:
    """Plain arithmetic: total cost across all logged entries divided
    by the distance actually covered between the first and last
    odometer reading. None if fewer than 2 entries — you can't compute
    a distance from a single point."""
    if len(entries) < 2:
        return None
    total_cost = sum(e.cost_usd for e in entries)
    miles_covered = entries[-1].odometer_miles - entries[0].odometer_miles
    if miles_covered <= 0:
        return None
    return round(total_cost / miles_covered, 4)


def compute_efficiency(entries: list[FuelLogEntry]) -> float | None:
    """Miles per gallon (gasoline) or miles per kWh (electric) between
    the first and last logged fill/charge. None if fewer than 2
    entries or mixed energy types (comparing gasoline miles-per-gallon
    against electric miles-per-kWh would be meaningless)."""
    if len(entries) < 2:
        return None
    energy_types = {e.energy_type for e in entries}
    if len(energy_types) > 1:
        return None
    miles_covered = entries[-1].odometer_miles - entries[0].odometer_miles
    total_quantity = sum(e.quantity for e in entries[1:])  # quantity used to cover the distance since the first reading
    if miles_covered <= 0 or total_quantity <= 0:
        return None
    return round(miles_covered / total_quantity, 2)


async def get_fuel_summary(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> dict:
    entries = await list_fuel_entries(db, tenant_id, vehicle_id)
    return {
        "vehicle_id": str(vehicle_id),
        "entry_count": len(entries),
        "total_cost_usd": round(sum(e.cost_usd for e in entries), 2),
        "cost_per_mile_usd": compute_cost_per_mile(entries),
        "efficiency": compute_efficiency(entries),
    }


async def get_fuel_entry(db: AsyncSession, tenant_id: str, entry_id: uuid.UUID) -> FuelLogEntry | None:
    result = await db.execute(select(FuelLogEntry).where(FuelLogEntry.id == entry_id, FuelLogEntry.tenant_id == tenant_id))
    return result.scalar_one_or_none()


def compute_implied_efficiency(previous_entry: FuelLogEntry, current_entry: FuelLogEntry) -> float | None:
    """Miles covered since the previous fill, divided by the quantity
    used in THIS fill only — an instant efficiency reading for this
    one entry, as opposed to compute_efficiency's average across the
    whole logged history. This is what makes a single anomalous entry
    detectable even when the vehicle's overall average still looks
    normal."""
    miles_since_last = current_entry.odometer_miles - previous_entry.odometer_miles
    if miles_since_last <= 0 or current_entry.quantity <= 0:
        return None
    return round(miles_since_last / current_entry.quantity, 2)


async def record_fuel_anomaly(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, fuel_log_entry_id: uuid.UUID, assessment: FuelAnomalyLevel, reasoning: str) -> FuelAnomalyAssessment:
    record = FuelAnomalyAssessment(tenant_id=tenant_id, vehicle_id=vehicle_id, fuel_log_entry_id=fuel_log_entry_id, assessment=assessment, reasoning=reasoning)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def auto_execute_fuel_anomaly(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, assessment: FuelAnomalyLevel, reasoning: str,
) -> "MaintenanceAlert | None":
    """Fully autonomous FLT-011 execution, chained straight into
    FLT-004: a FLAGGED fuel/energy anomaly (implausible efficiency, a
    cost spike, or a sharp deviation from the vehicle's own running
    average — any of which can mean a leak, siphoning, or a sensor
    fault) immediately opens a real maintenance alert, rather than
    sitting as an inert note waiting for someone to notice it."""
    if assessment != FuelAnomalyLevel.FLAGGED:
        return None
    from src.models import MaintenanceAlert, MaintenanceAlertSeverity

    alert = MaintenanceAlert(
        tenant_id=tenant_id, vehicle_id=vehicle_id, severity=MaintenanceAlertSeverity.PREDICTIVE,
        reason=f"Fuel/energy anomaly flagged by Fleet Intelligence Agent: {reasoning}", dtc_codes=[],
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def get_fuel_anomaly(db: AsyncSession, tenant_id: str, fuel_log_entry_id: uuid.UUID) -> FuelAnomalyAssessment | None:
    result = await db.execute(
        select(FuelAnomalyAssessment).where(FuelAnomalyAssessment.tenant_id == tenant_id, FuelAnomalyAssessment.fuel_log_entry_id == fuel_log_entry_id)
        .order_by(FuelAnomalyAssessment.computed_at.desc())
    )
    return result.scalars().first()
