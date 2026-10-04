"""
Pricing Intelligence Agent — RNT-002 Rental Fleet & Rate Management /
RNT-005 Revenue Analytics.

Reacts to rental.booked by reading rental-svc's own combined
supply+demand rate recommendation and deciding whether to apply it.
The actual multiplier math — utilization (supply) and booking
velocity/trend (demand) — lives entirely in rental-svc's
pricing_engine.py, not here: get_pricing_recommendation is the single
source of truth both the deterministic fallback and a live Claude
tool-use pass call into, so the formula is never duplicated in two
places that could quietly drift apart. Two hubs at identical
utilization but different demand trends must be priced differently —
that's the property this agent exists to act on, not to recompute.

Same two-layer discipline as the other four agents:

1. Deterministic fallback: fetches the real recommendation and applies
   it only if the change is meaningful. No API key required.
2. Live Claude path: reads utilization, demand forecast, and the
   reservation's current rate via tool use before recommending an
   adjustment — or simply defers to get_pricing_recommendation's own
   number, at its discretion.

apply_dynamic_rate (rental-svc's tool) only ever revises a rate before
checkout — rental-svc's own modify_reservation refuses the change once
a renter has taken the vehicle, so this agent can't retroactively alter
what someone already agreed to pay.
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.core.tiering import get_tier
from src.models import Agent, AgentStatus, DecisionLog
from src.services import dms_tools, rental_tools

AGENT_KEY = "pricing_intelligence_agent"

# Tier-policy agent key for rental rate adjustments (docs/AUTONOMY_
# TIERING_ENGINEER_INSTRUCTIONS.md §4) — tier 1, so this only tags the
# decision log for audit visibility (AI-004); it does not change
# execution, which was already immediate and correct for a tier-1 action.
PRICING_POLICY_AGENT = "pricing-intelligence"
RATE_ADJUST_ACTION_TYPE = "pricing.adjust_within_band"

SYSTEM_PROMPT = """You are the Pricing Intelligence Agent for VOYQI Rental.

A reservation was just booked. Use get_pricing_recommendation to see rental-svc's \
own combined supply (utilization) and demand (booking velocity/trend) rate \
recommendation for this reservation — the full breakdown and reasoning is computed \
there, not by you. You may also call get_utilization and get_demand_forecast \
directly if you want to inspect either signal on its own before deciding. Apply the \
recommended rate with apply_dynamic_rate only when the change is meaningful; not \
every booking needs a price change. Two hubs at the same utilization but different \
demand trends are in genuinely different positions — factor both signals in, not \
just utilization."""


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


@dataclass
class PricingRecommendationResult:
    decision_log: DecisionLog
    new_rate: dict[str, Any] | None
    skipped_reason: str | None = None


async def evaluate_rental_pricing(
    db: AsyncSession,
    tenant_id: str,
    reservation_id: str,
    hub_id: str,
    rental_class: str,
    client: AnthropicAgentClient | None = None,
) -> PricingRecommendationResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="rental.booked",
            trigger_context={"reservation_id": reservation_id, "hub_id": hub_id, "rental_class": rental_class},
            reasoning=f"Agent status is {status.value} — no pricing evaluation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return PricingRecommendationResult(decision_log=log, new_rate=None, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    new_rate: dict[str, Any] | None = None

    if client.is_configured:
        dispatch = rental_tools.build_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=SYSTEM_PROMPT,
            user_message=f"Reservation {reservation_id} booked at hub {hub_id}, class {rental_class}. Evaluate pricing.",
            tools=rental_tools.PRICING_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        reasoning = turn.final_text or "(agent produced no final text)"
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        rate_calls = [tc for tc in turn.tool_calls if tc.tool_name == "apply_dynamic_rate"]
        new_rate = rate_calls[-1].tool_output if rate_calls else None
    else:
        recommendation = rental_tools.get_pricing_recommendation(tenant_id, reservation_id)
        recommended_rate = recommendation.get("recommended_rate_usd", 0.0)
        current_rate = recommendation.get("current_rate_usd", 0.0)
        pct_change = abs(recommended_rate - current_rate) / current_rate * 100 if current_rate else 0

        tools_called = [{"tool": "get_pricing_recommendation", "input": {"reservation_id": reservation_id}}]

        if pct_change >= 1.0:
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured, no live Claude "
                f"reasoning was performed] {recommendation.get('reasoning', '')}"
            )
            new_rate = rental_tools.apply_dynamic_rate(tenant_id, reservation_id, recommended_rate)
            tools_called.append({"tool": "apply_dynamic_rate", "input": {"reservation_id": reservation_id, "new_daily_rate_usd": recommended_rate}})
        else:
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] "
                f"Combined supply+demand signal doesn't justify a change — base rate ${current_rate}/day held. {recommendation.get('reasoning', '')}"
            )

    action_taken = "dynamic_rate_applied" if new_rate else "base_rate_held"
    tier = get_tier(PRICING_POLICY_AGENT, RATE_ADJUST_ACTION_TYPE, {"reservation_id": reservation_id})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="rental.booked",
        trigger_context={"reservation_id": reservation_id, "hub_id": hub_id, "rental_class": rental_class},
        reasoning=reasoning, tools_called=tools_called, action_taken=action_taken, outcome="success",
        tier=tier, approval_required=False,
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return PricingRecommendationResult(decision_log=log, new_rate=new_rate)


async def handle_event(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> PricingRecommendationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_rental_pricing(
        db, tenant_id, payload.get("reservation_id", ""), payload.get("hub_id", ""), payload.get("rental_class", "")
    )


TRADE_APPRAISAL_SYSTEM_PROMPT = """You are the Pricing Intelligence Agent for VOYQI Auto Group, \
now evaluating a trade-in vehicle's market value.

Use get_market_comps to check for comparable sales data, but be aware VOYQI \
does not yet have a live market-comp data feed integrated — if the tool \
returns no comps, use your own general knowledge of typical used-vehicle \
values for this make/model/year/mileage/condition to produce a reasonable \
estimate. Always finish by calling submit_valuation with your number and \
your reasoning — if you relied on general knowledge rather than verified \
comps, say so plainly in the reasoning you submit. Your valuation is applied \
immediately, together with a wholesale offer computed from it — there is no \
separate human approval step for your own output."""


@dataclass
class TradeValuationResult:
    decision_log: DecisionLog
    valuation_usd: float | None
    offer_usd: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_trade_appraisal(
    db: AsyncSession,
    tenant_id: str,
    appraisal_id: str,
    vin: str,
    mileage: int,
    condition_notes: str,
    client: AnthropicAgentClient | None = None,
) -> TradeValuationResult:
    """DMS-005 — trade-in valuation, fully autonomous. Unlike the
    rental pricing path, there is genuinely no deterministic anchor to
    compute a valuation from without an API key: no market-comp data
    source is integrated yet, and dms-svc doesn't hold a reference
    price for a vehicle it doesn't own. Rather than fabricate a
    number, the no-key path is honest that it cannot value the
    vehicle; only the live Claude path (which can draw on general
    automotive market knowledge, clearly caveated as such) produces an
    actual estimate. Either way, a produced valuation is applied
    immediately via apply_trade_valuation — no separate acceptance
    step. The one thing this function checks before doing anything
    else: whether dms-svc has already put this appraisal under a
    security hold (a stolen-vehicle registry match), in which case it
    skips entirely, the same way it would for a paused agent — that
    hold is not something this agent has any path to override."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="trade_appraisal.valuation_requested",
            trigger_context={"appraisal_id": appraisal_id, "vin": vin},
            reasoning=f"Agent status is {status.value} — no valuation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return TradeValuationResult(decision_log=log, valuation_usd=None, offer_usd=None, reasoning=log.reasoning, skipped_reason=status.value)

    existing = dms_tools.get_appraisal(tenant_id, appraisal_id)
    if existing.get("status") == "security_hold":
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="trade_appraisal.valuation_requested",
            trigger_context={"appraisal_id": appraisal_id, "vin": vin},
            reasoning=(
                "This appraisal is under a security hold (stolen-vehicle registry match) set by dms-svc. "
                "This agent has no path to override that — no valuation was performed."
            ),
            tools_called=[], action_taken="skipped_security_hold", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return TradeValuationResult(decision_log=log, valuation_usd=None, offer_usd=None, reasoning=log.reasoning, skipped_reason="security_hold")

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    valuation_usd: float | None = None

    if client.is_configured:
        dispatch = dms_tools.build_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=TRADE_APPRAISAL_SYSTEM_PROMPT,
            user_message=f"VIN {vin}, mileage {mileage}, condition notes: {condition_notes}. Estimate trade-in value in USD.",
            tools=dms_tools.DMS_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        valuation_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_valuation"]
        if valuation_calls:
            submitted = valuation_calls[-1].tool_output
            valuation_usd = submitted.get("valuation_usd")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            reasoning = turn.final_text or "(agent produced no final text and never called submit_valuation)"
    else:
        reasoning = (
            "[deterministic fallback — ANTHROPIC_API_KEY not configured] No market-comp data source is "
            "integrated yet and dms-svc holds no reference price for a vehicle it doesn't own — a valuation "
            "cannot be produced deterministically. Route to a human appraiser or configure live Claude reasoning."
        )
        tools_called = []

    offer_usd: float | None = None
    if valuation_usd is not None:
        applied = dms_tools.apply_trade_valuation(tenant_id, appraisal_id, valuation_usd)
        offer_usd = applied.get("offer_usd")
        tools_called.append({"tool": "apply_trade_valuation", "input": {"appraisal_id": appraisal_id, "valuation_usd": valuation_usd}})

    action_taken = "valuation_and_offer_applied" if valuation_usd is not None else "valuation_not_available"

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="trade_appraisal.valuation_requested",
        trigger_context={"appraisal_id": appraisal_id, "vin": vin, "mileage": mileage},
        reasoning=reasoning, tools_called=tools_called, action_taken=action_taken, outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return TradeValuationResult(decision_log=log, valuation_usd=valuation_usd, offer_usd=offer_usd, reasoning=reasoning)


RETAIL_PRICING_SYSTEM_PROMPT = """You are the Pricing Intelligence Agent for VOYQI Auto Group, \
now recommending a retail asking price for a vehicle already in inventory.

Use get_book_value_average to check for real KBB/Black Book/JD Power/Manheim MMR \
book value data on this VIN — unlike trade-in appraisal, this data genuinely exists \
for many VINs. If a book value average exists, weigh it heavily. Factor in how long \
the vehicle has been in inventory (aging beyond the normal turn window justifies a \
markdown) and the vehicle's current asking price as your baseline anchor either way — \
you always have at least the current price and days-in-inventory to reason from, even \
with no book value on file. Always finish by calling submit_price_recommendation with \
your number and your reasoning — the price you submit is applied immediately, with no \
separate human approval step."""

AGING_MARKDOWN_PCT_PER_DAY_OVER = 0.005
AGING_MARKDOWN_CAP_PCT = 0.15


def _aging_markdown_pct(days_in_inventory: int, aging_threshold_days: int) -> float:
    days_over = max(0, days_in_inventory - aging_threshold_days)
    return min(AGING_MARKDOWN_CAP_PCT, days_over * AGING_MARKDOWN_PCT_PER_DAY_OVER)


@dataclass
class RetailPricingResult:
    decision_log: DecisionLog
    recommended_price_usd: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_retail_pricing(
    db: AsyncSession,
    tenant_id: str,
    retail_vehicle_id: str,
    vin: str,
    asking_price_usd: float,
    days_in_inventory: int,
    aging_threshold_days: int,
    client: AnthropicAgentClient | None = None,
) -> RetailPricingResult:
    """DMS-002/005 Market Pricing Service, fully autonomous. Unlike
    trade appraisal, this capability (almost) never comes back empty —
    days-in-inventory and the vehicle's own current asking price are
    always real, internal data, so the deterministic fallback below is
    a genuine, appliable recommendation, not an honest refusal. Book-
    value data (when it exists) refines it. Whatever price is produced
    is applied immediately via apply_retail_price — the same dms-svc
    endpoint the autonomous aging sweep itself calls, so this
    capability and the sweep leave an identical audit trail."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="retail_pricing.recommendation_requested",
            trigger_context={"retail_vehicle_id": retail_vehicle_id, "vin": vin},
            reasoning=f"Agent status is {status.value} — no pricing recommendation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return RetailPricingResult(decision_log=log, recommended_price_usd=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    recommended_price: float | None = None

    if client.is_configured:
        dispatch = dms_tools.build_retail_pricing_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=RETAIL_PRICING_SYSTEM_PROMPT,
            user_message=(
                f"VIN {vin}, current asking price ${asking_price_usd:,.0f}, {days_in_inventory} days in inventory "
                f"(aging threshold {aging_threshold_days} days). Recommend a retail asking price."
            ),
            tools=dms_tools.RETAIL_PRICING_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        price_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_price_recommendation"]
        if price_calls:
            submitted = price_calls[-1].tool_output
            recommended_price = submitted.get("recommended_price_usd")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            reasoning = turn.final_text or "(agent produced no final text and never called submit_price_recommendation)"
    else:
        book_value = dms_tools.get_book_value_average(tenant_id, vin)
        book_avg = book_value.get("average_retail_value_usd")
        anchor = book_avg if book_avg is not None else asking_price_usd
        markdown_pct = _aging_markdown_pct(days_in_inventory, aging_threshold_days)
        recommended_price = round(anchor * (1 - markdown_pct), 2)
        tools_called = [{"tool": "get_book_value_average", "input": {"vin": vin}}]

        anchor_desc = f"book-value average ${book_avg:,.0f}" if book_avg is not None else f"current asking price ${asking_price_usd:,.0f} (no book-value data on file)"
        reasoning = (
            "[deterministic fallback — ANTHROPIC_API_KEY not configured] "
            f"Anchor: {anchor_desc}. {days_in_inventory} days in inventory vs. {aging_threshold_days}-day threshold "
            f"-> aging markdown {markdown_pct * 100:.1f}%. Recommended price: ${recommended_price:,.0f}."
        )

    if recommended_price is not None:
        dms_tools.apply_retail_price(tenant_id, retail_vehicle_id, recommended_price, reasoning)
        tools_called.append({"tool": "apply_retail_price", "input": {"retail_vehicle_id": retail_vehicle_id, "price_usd": recommended_price}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="retail_pricing.recommendation_requested",
        trigger_context={"retail_vehicle_id": retail_vehicle_id, "vin": vin, "asking_price_usd": asking_price_usd, "days_in_inventory": days_in_inventory},
        reasoning=reasoning, tools_called=tools_called, action_taken="price_applied" if recommended_price is not None else "price_not_available", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return RetailPricingResult(decision_log=log, recommended_price_usd=recommended_price, reasoning=reasoning)


MAX_BID_SYSTEM_PROMPT = """You are the Pricing Intelligence Agent for VOYQI Auto Group, \
recommending a maximum auction bid for a vehicle the dealer group is considering acquiring.

Use get_book_value_average to check for real wholesale book value data on this VIN. \
A profitable acquisition needs room under wholesale for reconditioning cost and target \
front-end gross — bidding at full wholesale book leaves nothing. If book data exists, \
recommend a disciplined ceiling below the wholesale average. If no book data exists, \
say plainly that you cannot recommend a bid ceiling without a market anchor — do not \
invent one. Always finish by calling submit_max_bid with your recommendation (or your \
explicit inability to produce one, using a null recommendation)."""

MAX_BID_DISCOUNT_PCT = 0.08


@dataclass
class MaxBidResult:
    decision_log: DecisionLog
    max_bid_usd: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_max_bid(
    db: AsyncSession,
    tenant_id: str,
    listing_id: str,
    vin: str,
    condition_grade: float,
    client: AnthropicAgentClient | None = None,
) -> MaxBidResult:
    """DMS-011 max-bid recommendation — the fourth valuation surface on
    this agent, sitting between trade appraisal and retail pricing in
    how much real data it has. The wholesale book average (when quotes
    exist for the VIN) is a genuine deterministic anchor: the fallback
    recommends wholesale average minus a fixed acquisition discount.
    With no book data, it refuses honestly, exactly like trade
    appraisal — a bid ceiling invented without a market anchor is how
    dealers lose money at auction."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="max_bid.recommendation_requested",
            trigger_context={"listing_id": listing_id, "vin": vin},
            reasoning=f"Agent status is {status.value} — no max-bid recommendation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return MaxBidResult(decision_log=log, max_bid_usd=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    max_bid: float | None = None

    if client.is_configured:
        dispatch = dms_tools.build_max_bid_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=MAX_BID_SYSTEM_PROMPT,
            user_message=(
                f"VIN {vin}, auction condition grade {condition_grade:.1f}/5.0. "
                "Recommend a maximum bid ceiling for acquiring this vehicle."
            ),
            tools=dms_tools.MAX_BID_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        bid_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_max_bid"]
        if bid_calls:
            submitted = bid_calls[-1].tool_output
            max_bid = submitted.get("max_bid_usd")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            reasoning = turn.final_text or "(agent produced no final text and never called submit_max_bid)"
    else:
        book_value = dms_tools.get_book_value_average(tenant_id, vin)
        wholesale_avg = book_value.get("average_wholesale_value_usd")
        tools_called = [{"tool": "get_book_value_average", "input": {"vin": vin}}]
        if wholesale_avg is not None:
            max_bid = round(wholesale_avg * (1 - MAX_BID_DISCOUNT_PCT), 2)
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] "
                f"Wholesale book average ${wholesale_avg:,.0f} minus the {MAX_BID_DISCOUNT_PCT * 100:.0f}% acquisition "
                f"discount (room for recon cost and target front-end gross) -> recommended max bid ${max_bid:,.0f}."
            )
        else:
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] No wholesale book value data exists "
                "for this VIN — a bid ceiling cannot be recommended without a market anchor. Record book value "
                "quotes for this VIN first, or route to a human buyer."
            )

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="max_bid.recommendation_requested",
        trigger_context={"listing_id": listing_id, "vin": vin, "condition_grade": condition_grade},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="max_bid_recommendation_produced" if max_bid is not None else "no_recommendation_no_market_anchor",
        outcome="success" if max_bid is not None else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return MaxBidResult(decision_log=log, max_bid_usd=max_bid, reasoning=reasoning)
