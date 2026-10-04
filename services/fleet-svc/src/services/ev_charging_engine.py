"""
FLT-012 EV Charging & Battery Health Management.

Every commercial fleet platform researched (ChargePoint, Samsara,
Geotab's EV partners) treats charging-session tracking and battery
degradation monitoring as table-stakes for a modern fleet — this
closes that gap for VOYQI's electric vehicles.

Battery degradation alerts are plain threshold checks on state-of-health
readings, deliberately deterministic — the same principle as FLT-004's
maintenance alerts. A battery nearing end-of-life is consequential
enough (replacement cost, member-facing range) that the trigger needs
to be auditable arithmetic, not a model's judgment call.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import (
    BatteryDegradationForecast,
    BatteryHealthReading,
    ChargingSession,
    ChargingSessionStatus,
)


async def start_charging_session(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, station_location: str = "") -> ChargingSession:
    session = ChargingSession(tenant_id=tenant_id, vehicle_id=vehicle_id, station_location=station_location)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def complete_charging_session(db: AsyncSession, tenant_id: str, session_id: uuid.UUID, energy_added_kwh: float, cost_usd: float) -> ChargingSession | None:
    result = await db.execute(select(ChargingSession).where(ChargingSession.id == session_id, ChargingSession.tenant_id == tenant_id))
    session = result.scalar_one_or_none()
    if session is None or session.status != ChargingSessionStatus.IN_PROGRESS:
        return None
    session.status = ChargingSessionStatus.COMPLETED
    session.energy_added_kwh = energy_added_kwh
    session.cost_usd = cost_usd
    session.ended_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    return session


async def list_charging_sessions(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> list[ChargingSession]:
    result = await db.execute(select(ChargingSession).where(ChargingSession.tenant_id == tenant_id, ChargingSession.vehicle_id == vehicle_id))
    return list(result.scalars().all())


async def get_charging_cost_summary(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> dict:
    """Plain arithmetic: total energy and cost across completed
    sessions, and the real average cost per kWh — the actual metric
    every EV fleet platform researched highlights, not an estimate."""
    sessions = [s for s in await list_charging_sessions(db, tenant_id, vehicle_id) if s.status == ChargingSessionStatus.COMPLETED]
    total_kwh = sum(s.energy_added_kwh for s in sessions)
    total_cost = sum(s.cost_usd for s in sessions)
    return {
        "vehicle_id": str(vehicle_id),
        "session_count": len(sessions),
        "total_kwh": round(total_kwh, 2),
        "total_cost_usd": round(total_cost, 2),
        "avg_cost_per_kwh_usd": round(total_cost / total_kwh, 4) if total_kwh else None,
    }


async def record_battery_health(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, state_of_charge_pct: float, state_of_health_pct: float) -> BatteryHealthReading:
    reading = BatteryHealthReading(tenant_id=tenant_id, vehicle_id=vehicle_id, state_of_charge_pct=state_of_charge_pct, state_of_health_pct=state_of_health_pct)
    db.add(reading)
    await db.commit()
    await db.refresh(reading)
    return reading


async def get_latest_battery_health(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> BatteryHealthReading | None:
    result = await db.execute(
        select(BatteryHealthReading).where(BatteryHealthReading.tenant_id == tenant_id, BatteryHealthReading.vehicle_id == vehicle_id)
        .order_by(BatteryHealthReading.recorded_at.desc())
    )
    return result.scalars().first()


def is_degradation_alert(state_of_health_pct: float) -> bool:
    """Deterministic threshold — deliberately not AI, the same
    principle as maintenance alerts. Configurable via settings so
    fleet ops can tune it without a code change."""
    settings = get_settings()
    return state_of_health_pct < settings.battery_health_alert_threshold_pct


async def list_battery_degradation_watchlist(db: AsyncSession, tenant_id: str) -> list[BatteryHealthReading]:
    """Every vehicle's most recent reading, filtered to those below
    the degradation threshold — the EV-fleet equivalent of the FLT-010
    safety-score watchlist."""
    result = await db.execute(select(BatteryHealthReading).where(BatteryHealthReading.tenant_id == tenant_id))
    all_readings = list(result.scalars().all())
    latest_by_vehicle: dict[uuid.UUID, BatteryHealthReading] = {}
    for r in all_readings:
        existing = latest_by_vehicle.get(r.vehicle_id)
        if existing is None or r.recorded_at > existing.recorded_at:
            latest_by_vehicle[r.vehicle_id] = r
    return [r for r in latest_by_vehicle.values() if is_degradation_alert(r.state_of_health_pct)]


async def list_battery_health_readings(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> list[BatteryHealthReading]:
    result = await db.execute(
        select(BatteryHealthReading).where(BatteryHealthReading.tenant_id == tenant_id, BatteryHealthReading.vehicle_id == vehicle_id).order_by(BatteryHealthReading.recorded_at)
    )
    return list(result.scalars().all())


async def record_battery_degradation_forecast(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, degradation_rate_pct_per_day: float, estimated_days_to_threshold: int | None, reasoning: str,
) -> BatteryDegradationForecast:
    forecast = BatteryDegradationForecast(
        tenant_id=tenant_id, vehicle_id=vehicle_id, degradation_rate_pct_per_day=degradation_rate_pct_per_day, estimated_days_to_threshold=estimated_days_to_threshold, reasoning=reasoning,
    )
    db.add(forecast)
    await db.commit()
    await db.refresh(forecast)
    return forecast


# Inside this many projected days of crossing the SOH alert threshold,
# a forecast is urgent enough to open a real maintenance alert now
# rather than wait for the reactive threshold check to fire on its own.
DEGRADATION_URGENCY_WINDOW_DAYS = 30


async def auto_execute_battery_degradation_forecast(
    db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, estimated_days_to_threshold: int | None, reasoning: str,
) -> "MaintenanceAlert | None":
    """Fully autonomous FLT-012 execution, chained straight into
    FLT-004: when the trend projects crossing the SOH alert threshold
    within DEGRADATION_URGENCY_WINDOW_DAYS, a real maintenance alert
    opens immediately — proactive, before the reactive single-reading
    threshold check would otherwise fire."""
    if estimated_days_to_threshold is None or estimated_days_to_threshold > DEGRADATION_URGENCY_WINDOW_DAYS:
        return None
    from src.models import MaintenanceAlert, MaintenanceAlertSeverity

    alert = MaintenanceAlert(
        tenant_id=tenant_id, vehicle_id=vehicle_id, severity=MaintenanceAlertSeverity.PREDICTIVE,
        reason=f"Battery degradation trend projects SOH threshold crossing in ~{estimated_days_to_threshold} days: {reasoning}",
        dtc_codes=[],
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def get_latest_battery_degradation_forecast(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID) -> BatteryDegradationForecast | None:
    result = await db.execute(
        select(BatteryDegradationForecast).where(BatteryDegradationForecast.tenant_id == tenant_id, BatteryDegradationForecast.vehicle_id == vehicle_id)
        .order_by(BatteryDegradationForecast.computed_at.desc())
    )
    return result.scalars().first()
