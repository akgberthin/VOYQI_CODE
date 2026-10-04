from unittest.mock import MagicMock

from src.core.anthropic_client import AgentTurnResult, ToolCallResult
from src.models import Agent, AgentStatus
from src.services import pricing_intelligence_agent, rental_tools

TENANT = "voyqi-holdings"


class _UnconfiguredClient:
    is_configured = False


class _FakeConfiguredClient:
    is_configured = True

    def __init__(self, tool_output):
        self.run_agent_turn = MagicMock(
            return_value=AgentTurnResult(
                final_text="Utilization is high; applying a demand-adjusted rate.",
                tool_calls=[
                    ToolCallResult(tool_name="get_utilization", tool_input={"hub_id": "hub-1", "rental_class": "executive"}, tool_output={"utilization_pct": 84.0}),
                    ToolCallResult(tool_name="apply_dynamic_rate", tool_input={"reservation_id": "res-1", "new_daily_rate_usd": 163.0}, tool_output=tool_output),
                ],
                stop_reason="end_turn",
            )
        )


async def test_deterministic_fallback_applies_meaningful_recommendation(db_session, monkeypatch):
    monkeypatch.setattr(rental_tools, "get_pricing_recommendation", lambda *a, **k: {
        "current_rate_usd": 145.0, "recommended_rate_usd": 163.0, "reasoning": "util 90% -> supply 1.13x -> demand steady 1.0x -> combined 1.13x",
    })
    calls = []
    monkeypatch.setattr(
        rental_tools, "apply_dynamic_rate",
        lambda tenant_id, reservation_id, new_daily_rate_usd: calls.append((reservation_id, new_daily_rate_usd)) or {"id": "res-1", "daily_rate_usd": new_daily_rate_usd},
    )
    result = await pricing_intelligence_agent.evaluate_rental_pricing(
        db_session, TENANT, "res-1", "hub-1", "executive", client=_UnconfiguredClient()
    )
    assert result.new_rate is not None
    assert result.new_rate["daily_rate_usd"] == 163.0
    assert len(calls) == 1
    assert "deterministic fallback" in result.decision_log.reasoning


async def test_deterministic_fallback_holds_rate_when_change_negligible(db_session, monkeypatch):
    monkeypatch.setattr(rental_tools, "get_pricing_recommendation", lambda *a, **k: {
        "current_rate_usd": 65.0, "recommended_rate_usd": 65.2, "reasoning": "util 55% -> supply 1.0x -> demand steady 1.0x -> combined 1.0x",
    })
    calls = []
    monkeypatch.setattr(rental_tools, "apply_dynamic_rate", lambda *a, **k: calls.append(1) or {})
    result = await pricing_intelligence_agent.evaluate_rental_pricing(
        db_session, TENANT, "res-2", "hub-2", "urban", client=_UnconfiguredClient()
    )
    assert result.new_rate is None
    assert result.decision_log.action_taken == "base_rate_held"
    assert len(calls) == 0


async def test_paused_agent_evaluates_nothing(db_session, monkeypatch):
    agent_row = Agent(tenant_id=TENANT, key=pricing_intelligence_agent.AGENT_KEY, display_name="Pricing Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    calls = []
    monkeypatch.setattr(rental_tools, "get_pricing_recommendation", lambda *a, **k: calls.append(1) or {})
    result = await pricing_intelligence_agent.evaluate_rental_pricing(
        db_session, TENANT, "res-3", "hub-3", "urban", client=_UnconfiguredClient()
    )
    assert result.new_rate is None
    assert result.skipped_reason == "paused"
    assert len(calls) == 0


async def test_live_claude_path_uses_tool_calls(db_session, monkeypatch):
    monkeypatch.setattr(rental_tools, "build_tool_dispatch", lambda tenant_id: {"get_utilization": lambda inp: {}, "apply_dynamic_rate": lambda inp: {}})
    fake_client = _FakeConfiguredClient(tool_output={"id": "res-4", "daily_rate_usd": 163.0})
    result = await pricing_intelligence_agent.evaluate_rental_pricing(
        db_session, TENANT, "res-4", "hub-4", "executive", client=fake_client
    )
    fake_client.run_agent_turn.assert_called_once()
    assert result.new_rate == {"id": "res-4", "daily_rate_usd": 163.0}


async def test_agent_decision_count_increments(db_session, monkeypatch):
    monkeypatch.setattr(rental_tools, "get_pricing_recommendation", lambda *a, **k: {"current_rate_usd": 65.0, "recommended_rate_usd": 65.2, "reasoning": "steady"})
    agent_row = Agent(tenant_id=TENANT, key=pricing_intelligence_agent.AGENT_KEY, display_name="Pricing Intelligence Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await pricing_intelligence_agent.evaluate_rental_pricing(db_session, TENANT, "res-5", "hub-5", "urban", client=_UnconfiguredClient())
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_handle_event_adapter_extracts_payload_fields(db_session, monkeypatch):
    monkeypatch.setattr(rental_tools, "get_pricing_recommendation", lambda *a, **k: {"current_rate_usd": 65.0, "recommended_rate_usd": 65.2, "reasoning": "steady"})
    payload = {"reservation_id": "res-6", "hub_id": "hub-6", "rental_class": "urban"}
    result = await pricing_intelligence_agent.handle_event(db_session, TENANT, "rental.booked", payload)
    assert result.decision_log.trigger_context["reservation_id"] == "res-6"


async def test_trade_appraisal_deterministic_fallback_produces_no_valuation(db_session):
    # No market-comp data source exists and dms-svc holds no reference
    # price for a vehicle it doesn't own — the deterministic path is
    # honest that it cannot value the vehicle rather than fabricating a
    # number.
    result = await pricing_intelligence_agent.evaluate_trade_appraisal(
        db_session, TENANT, "appraisal-1", "1HGCM82633A004352", 45000, "minor wear", client=_UnconfiguredClient()
    )
    assert result.valuation_usd is None
    assert "cannot be produced deterministically" in result.reasoning


async def test_trade_appraisal_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=pricing_intelligence_agent.AGENT_KEY, display_name="Pricing Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await pricing_intelligence_agent.evaluate_trade_appraisal(
        db_session, TENANT, "appraisal-2", "1HGCM82633A004352", 45000, "x", client=_UnconfiguredClient()
    )
    assert result.valuation_usd is None
    assert result.skipped_reason == "paused"


async def test_trade_appraisal_live_claude_path_uses_submit_valuation_tool(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_appraisal", lambda tenant_id, appraisal_id: {"status": "pending"})
    monkeypatch.setattr(dms_tools, "apply_trade_valuation", lambda tenant_id, appraisal_id, valuation_usd: {"offer_usd": round(valuation_usd * 0.93, 2)})
    monkeypatch.setattr(dms_tools, "build_tool_dispatch", lambda tenant_id: {"get_market_comps": lambda inp: {}, "submit_valuation": lambda inp: {}})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Based on general market knowledge, I estimate this vehicle's value.",
                tool_calls=[
                    ToolCallResult(tool_name="get_market_comps", tool_input={"vin": "1HGCM82633A004352"}, tool_output={"comps": []}),
                    ToolCallResult(
                        tool_name="submit_valuation", tool_input={"valuation_usd": 14200.0, "reasoning": "general knowledge estimate, no comps available"},
                        tool_output={"valuation_usd": 14200.0, "reasoning": "general knowledge estimate, no comps available"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await pricing_intelligence_agent.evaluate_trade_appraisal(
        db_session, TENANT, "appraisal-3", "1HGCM82633A004352", 45000, "x", client=_FakeClient()
    )
    assert result.valuation_usd == 14200.0
    assert "general knowledge" in result.reasoning


async def test_retail_pricing_deterministic_fallback_uses_book_value(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": 65000.0, "average_wholesale_value_usd": 58000.0})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-1", "WDDUG8FB0KA123456", asking_price_usd=68000.0, days_in_inventory=10, aging_threshold_days=60,
        client=_UnconfiguredClient(),
    )
    assert result.recommended_price_usd == 65000.0  # no aging markdown yet, anchored on book value
    assert "book-value average" in result.reasoning


async def test_retail_pricing_falls_back_to_asking_price_without_book_value(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": None, "average_wholesale_value_usd": None})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-2", "1HGCM82633A004352", asking_price_usd=45000.0, days_in_inventory=10, aging_threshold_days=60,
        client=_UnconfiguredClient(),
    )
    assert result.recommended_price_usd == 45000.0
    assert "no book-value data on file" in result.reasoning


async def test_retail_pricing_applies_aging_markdown(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": None, "average_wholesale_value_usd": None})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    # 100 days in inventory, 60-day threshold -> 40 days over -> 40*0.5%=20%, capped at 15%
    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-3", "1HGCM82633A004352", asking_price_usd=50000.0, days_in_inventory=100, aging_threshold_days=60,
        client=_UnconfiguredClient(),
    )
    assert result.recommended_price_usd == 42500.0  # 50000 * 0.85 (capped at 15% markdown)


async def test_retail_pricing_never_refuses_unlike_trade_appraisal(db_session, monkeypatch):
    """The whole point of this capability vs. trade appraisal: there is
    always at least the asking price + aging to reason from."""
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": None, "average_wholesale_value_usd": None})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-4", "UNKNOWNVIN0000001", asking_price_usd=30000.0, days_in_inventory=0, aging_threshold_days=60,
        client=_UnconfiguredClient(),
    )
    assert result.recommended_price_usd is not None
    assert "cannot be produced" not in result.reasoning


async def test_retail_pricing_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=pricing_intelligence_agent.AGENT_KEY, display_name="Pricing Intelligence Agent", status=AgentStatus.DISABLED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-5", "1HGCM82633A004352", asking_price_usd=50000.0, days_in_inventory=10, aging_threshold_days=60,
        client=_UnconfiguredClient(),
    )
    assert result.recommended_price_usd is None
    assert result.skipped_reason == "disabled"


async def test_retail_pricing_live_claude_path_uses_submit_tool(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "build_retail_pricing_tool_dispatch", lambda tenant_id: {"get_book_value_average": lambda inp: {}, "submit_price_recommendation": lambda inp: {}})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Book value supports holding near asking price.",
                tool_calls=[
                    ToolCallResult(tool_name="get_book_value_average", tool_input={"vin": "WDDUG8FB0KA123456"}, tool_output={"average_retail_value_usd": 64000.0}),
                    ToolCallResult(
                        tool_name="submit_price_recommendation", tool_input={"recommended_price_usd": 63500.0, "reasoning": "book value with slight aging adjustment"},
                        tool_output={"recommended_price_usd": 63500.0, "reasoning": "book value with slight aging adjustment"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-6", "WDDUG8FB0KA123456", asking_price_usd=68000.0, days_in_inventory=10, aging_threshold_days=60,
        client=_FakeClient(),
    )
    assert result.recommended_price_usd == 63500.0


async def test_retail_pricing_decision_count_increments(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": None, "average_wholesale_value_usd": None})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    agent_row = Agent(tenant_id=TENANT, key=pricing_intelligence_agent.AGENT_KEY, display_name="Pricing Intelligence Agent")
    db_session.add(agent_row)
    await db_session.commit()

    await pricing_intelligence_agent.evaluate_retail_pricing(
        db_session, TENANT, "veh-7", "1HGCM82633A004352", asking_price_usd=50000.0, days_in_inventory=10, aging_threshold_days=60,
        client=_UnconfiguredClient(),
    )
    await db_session.refresh(agent_row)
    assert agent_row.decisions_made == 1


async def test_max_bid_deterministic_fallback_discounts_wholesale(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": 48000.0, "average_wholesale_value_usd": 42000.0})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    result = await pricing_intelligence_agent.evaluate_max_bid(
        db_session, TENANT, "listing-1", "WDDUG8FB0KA777777", condition_grade=4.2, client=_UnconfiguredClient()
    )
    assert result.max_bid_usd == 38640.0  # 42000 * 0.92
    assert "acquisition\ndiscount" in result.reasoning or "acquisition " in result.reasoning


async def test_max_bid_refuses_honestly_without_book_data(db_session, monkeypatch):
    """Same honesty contract as trade appraisal: no market anchor, no
    invented number — unlike retail pricing, an auction vehicle we
    don't own has no internal price to anchor on either."""
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "get_book_value_average", lambda tenant_id, vin: {"average_retail_value_usd": None, "average_wholesale_value_usd": None})
    monkeypatch.setattr(dms_tools, "apply_retail_price", lambda tenant_id, vehicle_id, price_usd, reasoning: {"price_usd": price_usd})
    result = await pricing_intelligence_agent.evaluate_max_bid(
        db_session, TENANT, "listing-2", "UNKNOWNVIN0000001", condition_grade=3.0, client=_UnconfiguredClient()
    )
    assert result.max_bid_usd is None
    assert "cannot be recommended without a market anchor" in result.reasoning
    assert result.decision_log.outcome == "deferred"


async def test_max_bid_paused_agent_produces_nothing(db_session):
    agent_row = Agent(tenant_id=TENANT, key=pricing_intelligence_agent.AGENT_KEY, display_name="Pricing Intelligence Agent", status=AgentStatus.PAUSED)
    db_session.add(agent_row)
    await db_session.commit()

    result = await pricing_intelligence_agent.evaluate_max_bid(
        db_session, TENANT, "listing-3", "WDDUG8FB0KA777777", condition_grade=4.0, client=_UnconfiguredClient()
    )
    assert result.max_bid_usd is None
    assert result.skipped_reason == "paused"


async def test_max_bid_live_claude_path_uses_submit_tool(db_session, monkeypatch):
    from src.services import dms_tools

    monkeypatch.setattr(dms_tools, "build_max_bid_tool_dispatch", lambda tenant_id: {"get_book_value_average": lambda inp: {}, "submit_max_bid": lambda inp: {}})

    class _FakeClient:
        is_configured = True

        def run_agent_turn(self, **kwargs):
            return AgentTurnResult(
                final_text="Wholesale supports a disciplined ceiling.",
                tool_calls=[
                    ToolCallResult(tool_name="get_book_value_average", tool_input={"vin": "WDDUG8FB0KA777777"}, tool_output={"average_wholesale_value_usd": 42000.0}),
                    ToolCallResult(
                        tool_name="submit_max_bid", tool_input={"max_bid_usd": 38000.0, "reasoning": "wholesale minus recon estimate and target gross"},
                        tool_output={"max_bid_usd": 38000.0, "reasoning": "wholesale minus recon estimate and target gross"},
                    ),
                ],
                stop_reason="end_turn",
            )

    result = await pricing_intelligence_agent.evaluate_max_bid(
        db_session, TENANT, "listing-4", "WDDUG8FB0KA777777", condition_grade=4.2, client=_FakeClient()
    )
    assert result.max_bid_usd == 38000.0
