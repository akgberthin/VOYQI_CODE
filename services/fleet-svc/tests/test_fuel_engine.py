import uuid

from src.models import EnergyType
from src.services import fuel_engine

TENANT = "voyqi-holdings"


async def test_log_fuel_entry(db_session):
    vehicle_id = uuid.uuid4()
    entry = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.5, 48.75, 24000)
    assert entry.quantity == 12.5


async def test_cost_per_mile_exact_arithmetic(db_session):
    vehicle_id = uuid.uuid4()
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 11.5, 43.0, 20400)

    entries = await fuel_engine.list_fuel_entries(db_session, TENANT, vehicle_id)
    cost_per_mile = fuel_engine.compute_cost_per_mile(entries)
    # total cost 88.0 / 400 miles covered
    assert cost_per_mile == round(88.0 / 400, 4)


async def test_cost_per_mile_none_with_single_entry(db_session):
    vehicle_id = uuid.uuid4()
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    entries = await fuel_engine.list_fuel_entries(db_session, TENANT, vehicle_id)
    assert fuel_engine.compute_cost_per_mile(entries) is None


async def test_efficiency_exact_arithmetic_gasoline(db_session):
    vehicle_id = uuid.uuid4()
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 10.0, 40.0, 20300)

    entries = await fuel_engine.list_fuel_entries(db_session, TENANT, vehicle_id)
    efficiency = fuel_engine.compute_efficiency(entries)
    # 300 miles covered / 10 gallons used since the first reading
    assert efficiency == 30.0


async def test_efficiency_none_for_mixed_energy_types(db_session):
    vehicle_id = uuid.uuid4()
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.ELECTRIC, 30.0, 9.0, 20300)

    entries = await fuel_engine.list_fuel_entries(db_session, TENANT, vehicle_id)
    assert fuel_engine.compute_efficiency(entries) is None


async def test_fuel_summary_aggregates_correctly(db_session):
    vehicle_id = uuid.uuid4()
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.ELECTRIC, 40.0, 12.0, 10000)
    await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.ELECTRIC, 38.0, 11.5, 10150)

    summary = await fuel_engine.get_fuel_summary(db_session, TENANT, vehicle_id)
    assert summary["entry_count"] == 2
    assert summary["total_cost_usd"] == 23.5
    assert summary["cost_per_mile_usd"] == round(23.5 / 150, 4)
    assert summary["efficiency"] == round(150 / 38.0, 2)


async def test_fuel_summary_no_entries(db_session):
    summary = await fuel_engine.get_fuel_summary(db_session, TENANT, uuid.uuid4())
    assert summary["entry_count"] == 0
    assert summary["cost_per_mile_usd"] is None


async def test_compute_implied_efficiency_exact_arithmetic(db_session):
    vehicle_id = uuid.uuid4()
    first = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    second = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 10.0, 40.0, 20300)

    efficiency = fuel_engine.compute_implied_efficiency(first, second)
    assert efficiency == 30.0  # 300 miles / 10 gallons


async def test_compute_implied_efficiency_zero_quantity_returns_none(db_session):
    vehicle_id = uuid.uuid4()
    first = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    second = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 0.0, 0.0, 20300)

    assert fuel_engine.compute_implied_efficiency(first, second) is None


async def test_compute_implied_efficiency_no_distance_returns_none(db_session):
    vehicle_id = uuid.uuid4()
    first = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    second = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 10.0, 40.0, 20000)

    assert fuel_engine.compute_implied_efficiency(first, second) is None


async def test_record_and_get_fuel_anomaly(db_session):
    from src.models import FuelAnomalyLevel

    vehicle_id = uuid.uuid4()
    entry = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    await fuel_engine.record_fuel_anomaly(db_session, TENANT, vehicle_id, entry.id, FuelAnomalyLevel.FLAGGED, "implausible efficiency")

    latest = await fuel_engine.get_fuel_anomaly(db_session, TENANT, entry.id)
    assert latest.assessment.value == "flagged"


async def test_get_fuel_anomaly_none_when_not_assessed(db_session):
    result = await fuel_engine.get_fuel_anomaly(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_get_fuel_entry(db_session):
    vehicle_id = uuid.uuid4()
    entry = await fuel_engine.log_fuel_entry(db_session, TENANT, vehicle_id, EnergyType.GASOLINE, 12.0, 45.0, 20000)
    fetched = await fuel_engine.get_fuel_entry(db_session, TENANT, entry.id)
    assert fetched.id == entry.id
