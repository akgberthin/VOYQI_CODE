from datetime import datetime, timedelta, timezone

from src.models import EnergyType, VehicleClass
from src.services import (
    analytics_engine,
    depreciation_engine,
    ev_charging_engine,
    fuel_engine,
    inventory_engine,
    maintenance_engine,
)

TENANT = "voyqi-holdings"


async def _certified_vehicle(db_session, vin="1HGCM82633A009999"):
    po = await inventory_engine.create_purchase_order(db_session, tenant_id=TENANT, source="dealer", vehicle_description="test", cost_usd=42000.0, created_by="fleet-ops-1")
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Test", model="Car", year=2024, vehicle_class=VehicleClass.EXECUTIVE, ownership_entity="x",
    )
    await inventory_engine.provision_vehicle(db_session, TENANT, vehicle.id, "fleet-ops-1", "VQ-1")
    await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")
    return vehicle


async def test_vehicle_tco_with_no_data_returns_none_total(db_session):
    vehicle = await _certified_vehicle(db_session)
    tco = await analytics_engine.get_vehicle_tco(db_session, TENANT, vehicle.id)
    assert tco["total_tco_usd"] is None  # no depreciation schedule on file — honest about the gap, not a guessed number
    assert tco["maintenance_cost_usd"] == 0.0


async def test_vehicle_tco_aggregates_all_cost_components(db_session):
    vehicle = await _certified_vehicle(db_session)
    await depreciation_engine.create_schedule(db_session, TENANT, vehicle.id, acquisition_cost_usd=42000.0, salvage_value_usd=12000.0, useful_life_months=60)

    order = await maintenance_engine.create_work_order(db_session, TENANT, vehicle.id, "VOYQI Certified Service", "fleet-ops-1")
    await maintenance_engine.complete_work_order(db_session, TENANT, order.id, cost_usd=500.0)

    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle.id, EnergyType.GASOLINE, 12.0, 45.0, 100)
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle.id, EnergyType.GASOLINE, 11.5, 43.0, 500)

    tco = await analytics_engine.get_vehicle_tco(db_session, TENANT, vehicle.id)
    assert tco["depreciation_to_date_usd"] == 0.0  # 0 months elapsed at schedule creation
    assert tco["maintenance_cost_usd"] == 500.0
    assert tco["energy_cost_usd"] == 88.0
    assert tco["total_tco_usd"] == 588.0


async def test_vehicle_tco_includes_charging_cost_for_ev(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A009998")
    await depreciation_engine.create_schedule(db_session, TENANT, vehicle.id, acquisition_cost_usd=48000.0, salvage_value_usd=15000.0, useful_life_months=60)

    session = await ev_charging_engine.start_charging_session(db_session, TENANT, vehicle.id)
    await ev_charging_engine.complete_charging_session(db_session, TENANT, session.id, energy_added_kwh=40.0, cost_usd=12.0)

    tco = await analytics_engine.get_vehicle_tco(db_session, TENANT, vehicle.id)
    assert tco["energy_cost_usd"] == 12.0


async def test_vehicle_availability_with_no_downtime_is_100_percent(db_session):
    vehicle = await _certified_vehicle(db_session)
    availability = await analytics_engine.get_vehicle_availability(db_session, TENANT, vehicle.id)
    assert availability["availability_pct"] == 100.0
    assert availability["maintenance_downtime_hours"] == 0.0


async def test_vehicle_availability_accounts_for_completed_downtime(db_session):
    vehicle = await _certified_vehicle(db_session)
    # Force acquired_at to exactly 100 hours ago so the math is exact and reproducible
    vehicle.acquired_at = datetime.now(timezone.utc) - timedelta(hours=100)
    await db_session.commit()

    order = await maintenance_engine.create_work_order(db_session, TENANT, vehicle.id, "VOYQI Certified Service", "fleet-ops-1")
    order.downtime_start = datetime.now(timezone.utc) - timedelta(hours=10)
    await db_session.commit()
    await maintenance_engine.complete_work_order(db_session, TENANT, order.id, cost_usd=200.0)
    # force downtime_end to exactly 5 hours after the forced downtime_start for a clean 5-hour window
    order.downtime_end = order.downtime_start + timedelta(hours=5)
    await db_session.commit()

    availability = await analytics_engine.get_vehicle_availability(db_session, TENANT, vehicle.id)
    assert availability["maintenance_downtime_hours"] == 5.0
    assert availability["availability_pct"] == round((1 - 5 / 100) * 100, 1)


async def test_vehicle_availability_unknown_vehicle(db_session):
    import uuid
    result = await analytics_engine.get_vehicle_availability(db_session, TENANT, uuid.uuid4())
    assert "error" in result


async def test_fleet_summary_counts_by_status_and_class(db_session):
    await _certified_vehicle(db_session, vin="1HGCM82633A000001")
    await _certified_vehicle(db_session, vin="1HGCM82633A000002")

    summary = await analytics_engine.get_fleet_summary(db_session, TENANT)
    assert summary["total_vehicles"] == 2
    assert summary["by_class"]["executive"] == 2
    assert summary["by_status"]["staged"] == 2


async def test_gather_fleet_optimization_signals_basic(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004460")
    await depreciation_engine.create_schedule(db_session, TENANT, vehicle.id, 60000.0, 20000.0, 36)

    signals = await analytics_engine.gather_fleet_optimization_signals(db_session, TENANT, vehicle.id)
    assert signals["vehicle_id"] == str(vehicle.id)
    assert signals["is_disposition_eligible"] is False  # fresh vehicle, low mileage
    assert signals["valuation_flagged"] is False  # no valuations recorded yet


async def test_gather_fleet_optimization_signals_reflects_flagged_valuation(db_session):
    from src.models import ValuationAnomalyLevel

    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004461")
    await depreciation_engine.create_schedule(db_session, TENANT, vehicle.id, 60000.0, 20000.0, 36)
    valuation = await depreciation_engine.record_market_valuation(db_session, TENANT, vehicle.id, 30000.0, "appraiser-1")
    await depreciation_engine.record_valuation_anomaly(db_session, TENANT, vehicle.id, valuation.id, -0.4, ValuationAnomalyLevel.FLAGGED, "well below book")

    signals = await analytics_engine.gather_fleet_optimization_signals(db_session, TENANT, vehicle.id)
    assert signals["valuation_flagged"] is True


async def test_record_and_get_latest_fleet_optimization(db_session):
    from src.models import FleetOptimizationRecommendation

    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004462")
    await analytics_engine.record_fleet_optimization(db_session, TENANT, vehicle.id, FleetOptimizationRecommendation.PRIORITIZE_DISPOSITION, "multiple negative signals converge")

    latest = await analytics_engine.get_latest_fleet_optimization(db_session, TENANT, vehicle.id)
    assert latest.recommendation.value == "prioritize_disposition"


async def test_get_latest_fleet_optimization_none_when_never_assessed(db_session):
    import uuid
    result = await analytics_engine.get_latest_fleet_optimization(db_session, TENANT, uuid.uuid4())
    assert result is None
