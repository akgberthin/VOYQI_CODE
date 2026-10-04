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
    tier = get_tier("concierge", "journey.premium_reroute", {"cost_delta_usd": 200})
    assert tier == 2  # already tier 2 regardless; threshold met, no escalation


def test_threshold_not_met_escalates_one_tier():
    """A threshold that isn't met must never silently pass — it
    escalates to the next tier up rather than guessing."""
    tier = get_tier("revenue", "term_conversion.offer_high_value", {"annual_value_usd": 500})
    assert tier == 3  # configured 2, threshold not met -> escalate to 3


def test_threshold_missing_context_field_escalates():
    tier = get_tier("revenue", "term_conversion.offer_high_value", {})
    assert tier == 3


def test_tier_3_is_never_escalated_past_3():
    tier = get_tier(
        "sales-originations-intelligence",
        "credit.decision",
        {"this_field_is_irrelevant": True},
    )
    assert tier == 3


def test_approval_required_helper():
    assert approval_required(1) is False
    assert approval_required(2) is True
    assert approval_required(3) is True


def test_requires_accountable_role_helper():
    assert requires_accountable_role(1) is False
    assert requires_accountable_role(2) is False
    assert requires_accountable_role(3) is True
