from src.models import DemandTrend
from src.services import pricing_engine


def test_supply_multiplier_neutral_in_middle_range():
    assert pricing_engine.compute_supply_multiplier(60.0) == 1.0


def test_supply_multiplier_scales_up_with_high_utilization():
    assert pricing_engine.compute_supply_multiplier(80.0) == 1.0
    assert pricing_engine.compute_supply_multiplier(100.0) == 1.25
    mid = pricing_engine.compute_supply_multiplier(90.0)
    assert 1.0 < mid < 1.25


def test_supply_multiplier_scales_down_with_low_utilization():
    assert pricing_engine.compute_supply_multiplier(40.0) == 1.0
    assert pricing_engine.compute_supply_multiplier(0.0) == 0.85
    mid = pricing_engine.compute_supply_multiplier(20.0)
    assert 0.85 < mid < 1.0


def test_demand_multiplier_accelerating_boosts_price():
    assert pricing_engine.compute_demand_multiplier(DemandTrend.ACCELERATING) > 1.0


def test_demand_multiplier_decelerating_reduces_price():
    assert pricing_engine.compute_demand_multiplier(DemandTrend.DECELERATING) < 1.0


def test_demand_multiplier_steady_is_neutral():
    assert pricing_engine.compute_demand_multiplier(DemandTrend.STEADY) == 1.0


def test_two_factor_model_diverges_from_utilization_only_model():
    """The entire point of this build: two hubs at IDENTICAL
    utilization but different demand trends must price differently.
    A utilization-only model would treat these two cases as
    indistinguishable; the combined model must not."""
    supply_mult = pricing_engine.compute_supply_multiplier(80.0)  # exactly at the neutral supply threshold
    accelerating_combined = supply_mult * pricing_engine.compute_demand_multiplier(DemandTrend.ACCELERATING)
    decelerating_combined = supply_mult * pricing_engine.compute_demand_multiplier(DemandTrend.DECELERATING)
    assert accelerating_combined != decelerating_combined
    assert accelerating_combined > 1.0  # accelerating demand alone justifies a premium even at neutral supply
    assert decelerating_combined < 1.0  # decelerating demand alone justifies a discount even at neutral supply
