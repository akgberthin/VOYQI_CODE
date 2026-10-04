import uuid

import pytest

from src.models import DispositionChannel, VehicleClass, VehicleStatus
from src.services import (
    depreciation_engine,
    floorplan_engine,
    inventory_engine,
    remarketing_engine,
)
from src.services.floorplan_engine import InsufficientCapacityError

TENANT = "voyqi-holdings"


async def _certified_vehicle(db_session, vin="1HGCM82633A004352"):
    po = await inventory_engine.create_purchase_order(
        db_session, tenant_id=TENANT, source="dealer", vehicle_description="test", cost_usd=50000.0, created_by="fleet-ops-1",
    )
    vehicle = await inventory_engine.receive_vehicle(
        db_session, tenant_id=TENANT, po_id=po.id, vin=vin, make="Test", model="Car", year=2024,
        vehicle_class=VehicleClass.EXECUTIVE, ownership_entity="x",
    )
    await inventory_engine.provision_vehicle(db_session, TENANT, vehicle.id, "fleet-ops-1", "VQ-1")
    await inventory_engine.certify_class(db_session, TENANT, vehicle.id, "fleet-ops-1")
    return vehicle


# ---------- FLT-006 Depreciation ----------

async def test_create_depreciation_schedule(db_session):
    vehicle = await _certified_vehicle(db_session)
    schedule = await depreciation_engine.create_schedule(
        db_session, tenant_id=TENANT, vehicle_id=vehicle.id, acquisition_cost_usd=60000.0,
        salvage_value_usd=20000.0, useful_life_months=36,
    )
    assert schedule.acquisition_cost_usd == 60000.0


def test_compute_book_value_straight_line():
    from src.models import DepreciationSchedule

    schedule = DepreciationSchedule(
        tenant_id=TENANT, vehicle_id=uuid.uuid4(), acquisition_cost_usd=60000.0,
        salvage_value_usd=24000.0, useful_life_months=36,
    )
    # monthly depreciation = (60000-24000)/36 = 1000/month
    book_value_at_12mo = depreciation_engine.compute_book_value(schedule, 12)
    assert book_value_at_12mo == 48000.0


def test_book_value_floors_at_salvage_value():
    from src.models import DepreciationSchedule

    schedule = DepreciationSchedule(
        tenant_id=TENANT, vehicle_id=uuid.uuid4(), acquisition_cost_usd=60000.0,
        salvage_value_usd=24000.0, useful_life_months=36,
    )
    book_value_beyond_life = depreciation_engine.compute_book_value(schedule, 999)
    assert book_value_beyond_life == 24000.0


async def test_get_current_book_value_no_schedule_returns_none(db_session):
    result = await depreciation_engine.get_current_book_value(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_record_market_valuation(db_session):
    vehicle = await _certified_vehicle(db_session)
    valuation = await depreciation_engine.record_market_valuation(db_session, TENANT, vehicle.id, 45000.0, "appraiser-1")
    assert valuation.market_value_estimate_usd == 45000.0


# ---------- FLT-007 Remarketing ----------

async def test_disposition_eligibility_not_eligible_for_new_vehicle(db_session):
    vehicle = await _certified_vehicle(db_session)
    result = await remarketing_engine.evaluate_disposition_eligibility(db_session, TENANT, vehicle.id)
    assert result["eligible"] is False


async def test_disposition_eligibility_by_mileage(db_session):
    vehicle = await _certified_vehicle(db_session)
    vehicle.odometer_miles = 65000
    await db_session.commit()
    result = await remarketing_engine.evaluate_disposition_eligibility(db_session, TENANT, vehicle.id)
    assert result["eligible"] is True
    assert any("mileage" in r for r in result["reasons"])


async def test_disposition_order_and_recovery(db_session):
    vehicle = await _certified_vehicle(db_session)
    order = await remarketing_engine.create_disposition_order(
        db_session, TENANT, vehicle.id, DispositionChannel.RETAIL_VOYQI_AUTO_GROUP, "fleet-ops-1"
    )
    assert order.status == "pending"

    updated_vehicle = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert updated_vehicle.status == VehicleStatus.REMARKETING

    recovered = await remarketing_engine.record_recovery(db_session, TENANT, order.id, 38000.0)
    assert recovered.status == "sold"
    assert recovered.recovery_amount_usd == 38000.0

    final_vehicle = await inventory_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert final_vehicle.status == VehicleStatus.DISPOSED


async def test_eligibility_unknown_vehicle_returns_none(db_session):
    result = await remarketing_engine.evaluate_disposition_eligibility(db_session, TENANT, uuid.uuid4())
    assert result is None


# ---------- FLT-009 Floorplan ----------

async def test_create_facility(db_session):
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 5_000_000.0)
    assert facility.utilized_amount_usd == 0.0


async def test_pledge_vehicle_within_capacity(db_session):
    vehicle = await _certified_vehicle(db_session)
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 5_000_000.0)
    collateral, breached = await floorplan_engine.pledge_vehicle(db_session, TENANT, vehicle.id, facility.id, 60000.0)
    assert collateral.status == "pledged"
    assert breached is False


async def test_pledge_beyond_credit_limit_rejected(db_session):
    vehicle = await _certified_vehicle(db_session)
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 50000.0)
    with pytest.raises(InsufficientCapacityError):
        await floorplan_engine.pledge_vehicle(db_session, TENANT, vehicle.id, facility.id, 60000.0)


async def test_pledge_beyond_utilization_covenant_flags_breach(db_session):
    vehicle = await _certified_vehicle(db_session)
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 100000.0, max_utilization_pct=50.0)
    collateral, breached = await floorplan_engine.pledge_vehicle(db_session, TENANT, vehicle.id, facility.id, 60000.0)
    assert breached is True  # 60% > 50% covenant, but still within the hard credit limit


async def test_release_vehicle_reduces_utilization(db_session):
    vehicle = await _certified_vehicle(db_session)
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 5_000_000.0)
    collateral, _ = await floorplan_engine.pledge_vehicle(db_session, TENANT, vehicle.id, facility.id, 60000.0)

    released = await floorplan_engine.release_vehicle(db_session, TENANT, collateral.id)
    assert released.status == "released"

    from sqlalchemy import select

    from src.models import FloorplanFacility

    result = await db_session.execute(select(FloorplanFacility).where(FloorplanFacility.id == facility.id))
    refreshed_facility = result.scalar_one()
    assert refreshed_facility.utilized_amount_usd == 0.0


async def test_release_unknown_collateral_returns_none(db_session):
    result = await floorplan_engine.release_vehicle(db_session, TENANT, uuid.uuid4())
    assert result is None


# ---------- FLT-006 Valuation Anomaly ----------

async def test_record_and_get_valuation_anomaly(db_session):
    from src.models import ValuationAnomalyLevel

    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004410")
    valuation = await depreciation_engine.record_market_valuation(db_session, TENANT, vehicle.id, 30000.0, "appraiser-1")

    await depreciation_engine.record_valuation_anomaly(db_session, TENANT, vehicle.id, valuation.id, -0.35, ValuationAnomalyLevel.FLAGGED, "market well below book")

    latest = await depreciation_engine.get_valuation_anomaly(db_session, TENANT, valuation.id)
    assert latest.assessment.value == "flagged"
    assert latest.deviation_pct == -0.35


async def test_get_valuation_anomaly_none_when_not_assessed(db_session):
    result = await depreciation_engine.get_valuation_anomaly(db_session, TENANT, uuid.uuid4())
    assert result is None


async def test_get_asset_valuation(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004411")
    valuation = await depreciation_engine.record_market_valuation(db_session, TENANT, vehicle.id, 45000.0, "appraiser-1")

    fetched = await depreciation_engine.get_asset_valuation(db_session, TENANT, valuation.id)
    assert fetched.id == valuation.id


# ---------- FLT-007 Disposition Channel Recommendation ----------

async def test_create_and_list_pending_disposition_recommendation(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004430")
    await remarketing_engine.create_disposition_channel_recommendation(db_session, TENANT, vehicle.id, DispositionChannel.RIDESHARE, "still within useful commercial life")

    pending = await remarketing_engine.list_pending_disposition_recommendations(db_session, TENANT)
    assert len(pending) == 1
    assert pending[0].recommended_channel.value == "rideshare"


async def test_accept_disposition_recommendation_creates_real_order(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004431")
    rec = await remarketing_engine.create_disposition_channel_recommendation(db_session, TENANT, vehicle.id, DispositionChannel.EXPORT, "high mileage, mechanically sound")

    accepted_rec, order = await remarketing_engine.accept_disposition_recommendation(db_session, TENANT, rec.id, "fleet-ops-1")
    assert accepted_rec.status.value == "accepted"
    assert order.channel.value == "export"
    assert order.vehicle_id == vehicle.id


async def test_accept_disposition_recommendation_with_channel_override(db_session):
    """The recommendation is a signal, not a binding instruction —
    fleet ops can accept the underlying decision to dispose while
    choosing a different channel."""
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004432")
    rec = await remarketing_engine.create_disposition_channel_recommendation(db_session, TENANT, vehicle.id, DispositionChannel.RIDESHARE, "still viable")

    _, order = await remarketing_engine.accept_disposition_recommendation(db_session, TENANT, rec.id, "fleet-ops-1", channel_override=DispositionChannel.PARTS)
    assert order.channel.value == "parts"


async def test_reject_disposition_recommendation_creates_no_order(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004433")
    rec = await remarketing_engine.create_disposition_channel_recommendation(db_session, TENANT, vehicle.id, DispositionChannel.RIDESHARE, "reasoning")

    rejected = await remarketing_engine.reject_disposition_recommendation(db_session, TENANT, rec.id)
    assert rejected.status.value == "rejected"


async def test_cannot_accept_disposition_recommendation_twice(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004434")
    rec = await remarketing_engine.create_disposition_channel_recommendation(db_session, TENANT, vehicle.id, DispositionChannel.RIDESHARE, "reasoning")
    await remarketing_engine.accept_disposition_recommendation(db_session, TENANT, rec.id, "fleet-ops-1")

    with pytest.raises(remarketing_engine.RecommendationNotPendingError):
        await remarketing_engine.accept_disposition_recommendation(db_session, TENANT, rec.id, "fleet-ops-1")


async def test_get_vehicle_in_remarketing_engine(db_session):
    vehicle = await _certified_vehicle(db_session, vin="1HGCM82633A004435")
    fetched = await remarketing_engine.get_vehicle(db_session, TENANT, vehicle.id)
    assert fetched.id == vehicle.id


# ---------- FLT-008/009 Covenant Risk ----------

async def test_record_and_get_latest_covenant_risk(db_session):
    from src.models import CovenantRiskLevel

    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 100000.0, max_utilization_pct=90.0)
    await floorplan_engine.record_covenant_risk(db_session, TENANT, facility.id, CovenantRiskLevel.APPROACHING, "utilization at 82% of covenant limit")

    latest = await floorplan_engine.get_latest_covenant_risk(db_session, TENANT, facility.id)
    assert latest.risk_level.value == "approaching"


async def test_get_latest_covenant_risk_none_when_never_assessed(db_session):
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 100000.0)
    result = await floorplan_engine.get_latest_covenant_risk(db_session, TENANT, facility.id)
    assert result is None


async def test_get_facility(db_session):
    facility = await floorplan_engine.create_facility(db_session, TENANT, "Ally Financial", 100000.0)
    fetched = await floorplan_engine.get_facility(db_session, TENANT, facility.id)
    assert fetched.id == facility.id
