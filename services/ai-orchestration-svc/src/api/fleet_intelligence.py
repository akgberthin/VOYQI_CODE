from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import fleet_intelligence_agent

router = APIRouter(prefix="/api/v1/ai/fleet", tags=["fleet-intelligence-agent"])


class StagingEvaluationRequest(BaseModel):
    event_type: str
    vehicle_id: str
    destination_hub_id: str
    context: dict[str, Any] = {}


@router.post("/evaluate-staging")
async def evaluate_staging(
    body: StagingEvaluationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_staging_need(
        db, tenant_id=tenant_id, event_type=body.event_type, vehicle_id=body.vehicle_id,
        destination_hub_id=body.destination_hub_id, context=body.context,
    )
    return {
        "proposed_transfer": result.proposed_transfer,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class HubRebalancingRequest(BaseModel):
    source_hub_id: str
    reason: str = "network-wide rebalancing check"


@router.post("/evaluate-hub-rebalancing")
async def evaluate_hub_rebalancing(
    body: HubRebalancingRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_hub_rebalancing(
        db, tenant_id=tenant_id, source_hub_id=body.source_hub_id, reason=body.reason,
    )
    return {
        "candidate_hub_id": result.candidate_hub_id,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class SafetyScoreRequest(BaseModel):
    vehicle_id: str
    event_counts: dict[str, int] = {}
    window_days: int = 30


@router.post("/evaluate-safety-score")
async def evaluate_safety_score(
    body: SafetyScoreRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_safety_score(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, event_counts=body.event_counts, window_days=body.window_days,
    )
    return {
        "score": result.score,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class IntakeReadinessRequest(BaseModel):
    intake_inspection_id: str
    passed: bool
    notes: str = ""


@router.post("/evaluate-intake-readiness")
async def evaluate_intake_readiness(
    body: IntakeReadinessRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_intake_readiness(
        db, tenant_id=tenant_id, intake_inspection_id=body.intake_inspection_id, passed=body.passed, notes=body.notes,
    )
    return {
        "recommendation": result.recommendation,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class TelemetryAnomalyRequest(BaseModel):
    geofence_alert_id: str
    distance_miles: float


@router.post("/evaluate-telemetry-anomaly")
async def evaluate_telemetry_anomaly(
    body: TelemetryAnomalyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_telemetry_anomaly(
        db, tenant_id=tenant_id, geofence_alert_id=body.geofence_alert_id, distance_miles=body.distance_miles,
    )
    return {
        "assessment": result.assessment,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class MaintenanceTriageRequest(BaseModel):
    alerts: list[dict] = []


@router.post("/evaluate-maintenance-triage")
async def evaluate_maintenance_triage(
    body: MaintenanceTriageRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_maintenance_triage(db, tenant_id=tenant_id, alerts=body.alerts)
    return {
        "priority_order": result.priority_order,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class IncidentSeverityRequest(BaseModel):
    incident_id: str
    incident_type: str
    description: str = ""


@router.post("/evaluate-incident-severity")
async def evaluate_incident_severity(
    body: IncidentSeverityRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_incident_severity(
        db, tenant_id=tenant_id, incident_id=body.incident_id, incident_type=body.incident_type, description=body.description,
    )
    return {
        "severity": result.severity,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class ValuationAnomalyRequest(BaseModel):
    vehicle_id: str
    asset_valuation_id: str
    book_value_usd: float
    market_value_usd: float


@router.post("/evaluate-valuation-anomaly")
async def evaluate_valuation_anomaly(
    body: ValuationAnomalyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_valuation_anomaly(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, asset_valuation_id=body.asset_valuation_id, book_value_usd=body.book_value_usd, market_value_usd=body.market_value_usd,
    )
    return {
        "assessment": result.assessment,
        "deviation_pct": result.deviation_pct,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class DispositionChannelRequest(BaseModel):
    vehicle_id: str
    odometer_miles: int
    vehicle_class: str = ""


@router.post("/evaluate-disposition-channel")
async def evaluate_disposition_channel(
    body: DispositionChannelRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_disposition_channel(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, odometer_miles=body.odometer_miles, vehicle_class=body.vehicle_class,
    )
    return {
        "recommended_channel": result.recommended_channel,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class CovenantRiskRequest(BaseModel):
    facility_id: str
    utilization_pct: float
    max_utilization_pct: float = 90.0


@router.post("/evaluate-covenant-risk")
async def evaluate_covenant_risk(
    body: CovenantRiskRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_covenant_risk(
        db, tenant_id=tenant_id, facility_id=body.facility_id, utilization_pct=body.utilization_pct, max_utilization_pct=body.max_utilization_pct,
    )
    return {
        "risk_level": result.risk_level,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class FuelAnomalyRequest(BaseModel):
    vehicle_id: str
    fuel_log_entry_id: str
    energy_type: str = "gasoline"
    implied_efficiency: float | None = None
    cost_per_unit_usd: float | None = None


@router.post("/evaluate-fuel-anomaly")
async def evaluate_fuel_anomaly(
    body: FuelAnomalyRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_fuel_anomaly(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, fuel_log_entry_id=body.fuel_log_entry_id, energy_type=body.energy_type,
        implied_efficiency=body.implied_efficiency, cost_per_unit_usd=body.cost_per_unit_usd,
    )
    return {
        "assessment": result.assessment,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class BatteryDegradationTrendRequest(BaseModel):
    vehicle_id: str
    first_soh_pct: float
    latest_soh_pct: float
    days_elapsed: float
    threshold_pct: float = 80.0


@router.post("/evaluate-battery-degradation-trend")
async def evaluate_battery_degradation_trend(
    body: BatteryDegradationTrendRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_battery_degradation_trend(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, first_soh_pct=body.first_soh_pct, latest_soh_pct=body.latest_soh_pct,
        days_elapsed=body.days_elapsed, threshold_pct=body.threshold_pct,
    )
    return {
        "estimated_days_to_threshold": result.estimated_days_to_threshold,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }


class FleetOptimizationRequest(BaseModel):
    vehicle_id: str
    total_tco_usd: float | None = None
    availability_pct: float | None = None
    is_disposition_eligible: bool = False
    valuation_flagged: bool = False


@router.post("/evaluate-fleet-optimization")
async def evaluate_fleet_optimization(
    body: FleetOptimizationRequest, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    result = await fleet_intelligence_agent.evaluate_fleet_optimization(
        db, tenant_id=tenant_id, vehicle_id=body.vehicle_id, total_tco_usd=body.total_tco_usd, availability_pct=body.availability_pct,
        is_disposition_eligible=body.is_disposition_eligible, valuation_flagged=body.valuation_flagged,
    )
    return {
        "recommendation": result.recommendation,
        "reasoning": result.reasoning,
        "skipped_reason": result.skipped_reason,
        "decision_log_id": str(result.decision_log.id),
    }
