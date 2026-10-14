from src.core.tiering import approval_required, get_tier, requires_accountable_role


def test_known_tier_1_action_returns_1():
    assert get_tier("pricing-intelligence", "pricing.adjust_within_band", {}) == 1


def test_known_tier_3_action_returns_3():
    assert get_tier("sales-originations-intelligence", "credit.decision", {}) == 3


def test_unknown_action_falls_back_to_default_tier():
    """Silence must never mean autonomous — an action type nobody has
    explicitly classified defaults to tier 2 (approval required), not
    tier 1."""
    assert get_tier("pricing-intelligence", "some_action_nobody_classified", {}) == 2


def test_unknown_agent_falls_back_to_default_tier():
    assert get_tier("an-agent-that-does-not-exist", "whatever", {}) == 2


def test_threshold_met_keeps_configured_tier():
    assert get_tier("concierge", "journey.premium_reroute", {"cost_delta_usd": 200}) == 2


def test_threshold_clearly_not_met_lowers_one_tier():
    """A $40 reroute is below the bar that warranted the approval gate."""
    assert get_tier("concierge", "journey.premium_reroute", {"cost_delta_usd": 40}) == 1


def test_ambiguous_threshold_keeps_configured_tier():
    """Missing or non-numeric context is never read as "not met" — it
    must not drop the action below its configured tier."""
    assert get_tier("revenue", "term_conversion.offer_high_value", {}) == 2
    assert get_tier("revenue", "term_conversion.offer_high_value", {"annual_value_usd": "lots"}) == 2


def test_threshold_never_raises_an_action_into_tier_3():
    """Tier 3 is a regulatory category, not a bigger number: no value of
    a threshold field can put an ordinary action there."""
    for value in (0, 500, 10_000, 10**9):
        assert get_tier("revenue", "term_conversion.offer_high_value", {"annual_value_usd": value}) in (1, 2)


def test_unevaluable_policy_threshold_keeps_configured_tier():
    """The client policy carries a prose threshold ("within AI-005
    guardrails") — it can't be evaluated, so the tier stays as set."""
    assert get_tier("pricing-intelligence", "pricing.demand_multiplier_apply", {}) == 1


def test_tier_3_is_never_lowered_or_raised():
    assert get_tier("sales-originations-intelligence", "credit.decision", {"this_field_is_irrelevant": True}) == 3


def test_approval_required_helper():
    assert approval_required(1) is False
    assert approval_required(2) is True
    assert approval_required(3) is True


def test_requires_accountable_role_helper():
    assert requires_accountable_role(1) is False
    assert requires_accountable_role(2) is False
    assert requires_accountable_role(3) is True
