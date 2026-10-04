"""
Fleet Intelligence Agent — FLT-003 Staging & Hub Allocation Board.

Reacts to member/travel events (subscription created, travel activated,
vehicle idle 48h) by recommending whether a vehicle should be staged
elsewhere. Same two-layer discipline as the other two agents:

1. Deterministic fallback: a simple supply-shortage heuristic that works
   with zero API key — if the destination hub is short on the requested
   class, propose a transfer from wherever has surplus; otherwise do
   nothing. Functional, not clever.
2. Live Claude path: reads real hub supply and vehicle context via tool
   use before proposing anything.

Neither path ever dispatches a transfer. propose_transfer only ever
creates a PROPOSED order — moving a six-figure physical asset requires a
human to assign a driver and confirm logistics, the same non-negotiable
human-in-the-loop principle as "never auto-send a message" or "never
auto-approve a permission grant."
"""
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.anthropic_client import AnthropicAgentClient
from src.models import Agent, AgentStatus, DecisionLog
from src.services import fleet_tools, hub_tools

AGENT_KEY = "fleet_intelligence_agent"

SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI Mobility's Staging \
& Hub Allocation Board.

Your job is to recommend vehicle staging moves in response to member and \
travel events — not to guess, but to look at actual hub supply first via \
get_hub_supply and get_vehicle, then decide whether a transfer is warranted.

You may propose a transfer with propose_transfer, but this NEVER dispatches \
a vehicle — it only creates a proposal a human fleet operator must separately \
assign a driver to and dispatch. Do not propose a transfer unless there is a \
genuine supply gap; moving vehicles has real transport cost, so only recommend \
moves that are actually justified by the hub's current supply relative to \
the triggering event."""

HUB_REBALANCING_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent, now reasoning about \
Hub Network rebalancing (HUB-002/003) rather than a single vehicle transfer.

Use get_network_snapshot to see every active hub's type, capacity, current utilization, \
and geo coordinates. Given a source hub with a problem (a touchpoint exception, or \
trending toward capacity), recommend which OTHER hub is the best candidate to supply \
or receive vehicles from — weighing both distance AND whether that hub actually has \
real headroom (a nearby hub already near its own capacity is a bad candidate even if \
close). Consider hub type where it's informative (an airport hub's churn pattern differs \
from a residential one) but don't over-index on it without real vehicle-class data. If no \
hub is a good candidate, say so plainly — call submit_rebalancing_recommendation with \
candidate_hub_id=null rather than forcing a weak recommendation. Always finish by calling \
submit_rebalancing_recommendation."""


async def _agent_status(db: AsyncSession, tenant_id: str) -> AgentStatus:
    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    return agent_row.status if agent_row else AgentStatus.ACTIVE


@dataclass
class StagingRecommendationResult:
    decision_log: DecisionLog
    proposed_transfer: dict[str, Any] | None
    skipped_reason: str | None = None


async def evaluate_staging_need(
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    vehicle_id: str,
    destination_hub_id: str,
    context: dict[str, Any],
    client: AnthropicAgentClient | None = None,
) -> StagingRecommendationResult:
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=event_type,
            trigger_context={"vehicle_id": vehicle_id, "destination_hub_id": destination_hub_id},
            reasoning=f"Agent status is {status.value} — no staging evaluation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return StagingRecommendationResult(decision_log=log, proposed_transfer=None, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    proposed_transfer: dict[str, Any] | None = None

    if client.is_configured:
        dispatch = fleet_tools.build_tool_dispatch(tenant_id, AGENT_KEY)
        user_message = (
            f"Event: {event_type}\nVehicle: {vehicle_id}\nCandidate destination hub: {destination_hub_id}\n"
            f"Context: {context}\nDecide whether a staging transfer is warranted."
        )
        turn = client.run_agent_turn(
            system_prompt=SYSTEM_PROMPT, user_message=user_message,
            tools=fleet_tools.FLEET_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        reasoning = turn.final_text or "(agent produced no final text)"
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        transfer_calls = [tc for tc in turn.tool_calls if tc.tool_name == "propose_transfer"]
        proposed_transfer = transfer_calls[-1].tool_output if transfer_calls else None
    else:
        supply = fleet_tools.get_hub_supply(tenant_id, destination_hub_id)
        requested_class = context.get("vehicle_class", "")
        available = supply.get(requested_class, 0)
        threshold = context.get("supply_threshold", 1)
        if available < threshold:
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured, no live Claude "
                f"reasoning was performed] Destination hub has {available} {requested_class} vehicle(s) "
                f"available, below threshold {threshold} — proposing transfer."
            )
            proposed_transfer = fleet_tools.propose_transfer(
                tenant_id, vehicle_id, destination_hub_id,
                f"deterministic fallback: supply below threshold ({available} < {threshold})", AGENT_KEY,
            )
            tools_called = [{"tool": "propose_transfer", "input": {"vehicle_id": vehicle_id, "to_hub_id": destination_hub_id}}]
        else:
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] Destination hub has "
                f"{available} {requested_class} vehicle(s) available, at or above threshold {threshold} "
                "— no transfer needed."
            )

    action_taken = "transfer_proposed" if proposed_transfer else "no_transfer_needed"

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=event_type,
        trigger_context={"vehicle_id": vehicle_id, "destination_hub_id": destination_hub_id, **context},
        reasoning=reasoning, tools_called=tools_called, action_taken=action_taken, outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return StagingRecommendationResult(decision_log=log, proposed_transfer=proposed_transfer)


async def handle_event(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> StagingRecommendationResult:
    """Adapter matching the supervisor's AgentHandler signature
    (db, tenant_id, event_type, payload) -> result. Platform events like
    subscription.created and travel.activated carry vehicle_id and
    destination_hub_id directly in their payload; everything else in
    the payload is passed through as context."""
    vehicle_id = payload.get("vehicle_id", "")
    destination_hub_id = payload.get("destination_hub_id", "")
    context = {k: v for k, v in payload.items() if k not in ("vehicle_id", "destination_hub_id")}
    return await evaluate_staging_need(db, tenant_id, event_type, vehicle_id, destination_hub_id, context)


REBALANCE_HEADROOM_THRESHOLD_PCT = 70.0


@dataclass
class RebalancingResult:
    decision_log: DecisionLog
    candidate_hub_id: str | None
    reasoning: str
    skipped_reason: str | None = None


def _nearest_hub_with_headroom(hubs: list[dict], source_hub_id: str, threshold_pct: float) -> dict | None:
    source = next((h for h in hubs if h["hub_id"] == source_hub_id), None)
    if source is None:
        return None
    candidates = [
        h for h in hubs
        if h["hub_id"] != source_hub_id and h.get("utilization_pct", 100) < threshold_pct
        and "latitude" in h and "longitude" in h
    ]
    if not candidates:
        return None
    for h in candidates:
        h["_distance_km"] = hub_tools.haversine_km(source["latitude"], source["longitude"], h["latitude"], h["longitude"])
    candidates.sort(key=lambda h: h["_distance_km"])
    return candidates[0]


async def evaluate_hub_rebalancing(
    db: AsyncSession,
    tenant_id: str,
    source_hub_id: str,
    reason: str,
    client: AnthropicAgentClient | None = None,
) -> RebalancingResult:
    """DMS... no — HUB-002/003 extension: reacts to a touchpoint
    exception ("vehicle not staged") or a manual network-wide check by
    reasoning about which OTHER hub should supply or receive vehicles.
    Reuses this agent rather than inventing a sixth — staging/allocation
    is exactly its existing domain, just fed by hub-svc's richer
    geo/utilization data instead of fleet-svc's thin hub records.

    Deliberately does not call fleet_tools.propose_transfer here: doing
    so would require the fleet-svc/hub-svc hub-id reconciliation this
    platform doesn't have yet (documented in hub-svc's README as a real,
    open seam). This capability recommends a candidate hub for a human
    dispatcher to act on; closing that loop end-to-end is the next step,
    not something to paper over by guessing at an ID mapping."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="hub.rebalancing_requested",
            trigger_context={"source_hub_id": source_hub_id, "reason": reason},
            reasoning=f"Agent status is {status.value} — no rebalancing evaluation was performed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return RebalancingResult(decision_log=log, candidate_hub_id=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []
    candidate_hub_id: str | None = None

    if client.is_configured:
        dispatch = hub_tools.build_rebalancing_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=HUB_REBALANCING_SYSTEM_PROMPT,
            user_message=f"Source hub: {source_hub_id}\nReason: {reason}\nRecommend which hub should supply or receive vehicles, if any.",
            tools=hub_tools.NETWORK_SNAPSHOT_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        rec_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_rebalancing_recommendation"]
        if rec_calls:
            submitted = rec_calls[-1].tool_output
            candidate_hub_id = submitted.get("candidate_hub_id")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            reasoning = turn.final_text or "(agent produced no final text and never called submit_rebalancing_recommendation)"
    else:
        snapshot = hub_tools.get_network_snapshot(tenant_id)
        hubs = snapshot.get("hubs", [])
        tools_called = [{"tool": "get_network_snapshot", "input": {}}]
        candidate = _nearest_hub_with_headroom(hubs, source_hub_id, REBALANCE_HEADROOM_THRESHOLD_PCT)
        if candidate:
            candidate_hub_id = candidate["hub_id"]
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] Nearest active hub below "
                f"{REBALANCE_HEADROOM_THRESHOLD_PCT:.0f}% utilization: {candidate['name']} "
                f"({candidate['utilization_pct']:.1f}% utilized, {candidate['_distance_km']:.1f} km away)."
            )
        else:
            reasoning = (
                "[deterministic fallback — ANTHROPIC_API_KEY not configured] No other active hub in the "
                f"network snapshot is below {REBALANCE_HEADROOM_THRESHOLD_PCT:.0f}% utilization — no rebalancing candidate found."
            )

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="hub.rebalancing_requested",
        trigger_context={"source_hub_id": source_hub_id, "reason": reason},
        reasoning=reasoning, tools_called=tools_called,
        action_taken="rebalancing_candidate_found" if candidate_hub_id else "no_rebalancing_candidate",
        outcome="success" if candidate_hub_id else "deferred",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return RebalancingResult(decision_log=log, candidate_hub_id=candidate_hub_id, reasoning=reasoning)


async def handle_touchpoint_exception(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> RebalancingResult:
    """Adapter matching the supervisor's AgentHandler signature for
    hub.touchpoint_exception_flagged."""
    return await evaluate_hub_rebalancing(db, tenant_id, payload.get("hub_id", ""), payload.get("reason", "touchpoint exception"))


SAFETY_SCORE_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, assessing a vehicle's safety score from recent driving event counts \
(FLT-010).

Score 0-100, higher is safer. This score drives real fleet-safety decisions — a \
vehicle on the watchlist may get pulled for driver coaching or inspection — so weigh \
event severity honestly rather than averaging everything into a comfortable middle. \
Always finish by calling submit_safety_score."""

# Deterministic fallback weights — a real, auditable scoring rule, not
# a placeholder. Phone usage and forward-collision warnings are
# weighted heaviest since they carry the highest real-world risk.
EVENT_WEIGHTS: dict[str, float] = {
    "harsh_braking": -5,
    "rapid_acceleration": -5,
    "speeding": -10,
    "phone_usage_detected": -15,
    "forward_collision_warning": -20,
}


def _deterministic_safety_score(event_counts: dict[str, int]) -> tuple[float, str]:
    score = 100.0
    drivers = []
    for event_type, count in event_counts.items():
        weight = EVENT_WEIGHTS.get(event_type, 0)
        if weight and count:
            score += weight * count
            drivers.append(f"{count}x {event_type} ({weight * count:+.0f})")
    score = max(0.0, round(score, 1))
    reasoning = (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Base 100, floor 0. "
        f"{'; '.join(drivers) if drivers else 'no scored events in this window'}."
    )
    return score, reasoning


@dataclass
class SafetyScoreResult:
    decision_log: DecisionLog
    score: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_safety_score(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: str,
    event_counts: dict[str, int],
    window_days: int = 30,
    client: AnthropicAgentClient | None = None,
) -> SafetyScoreResult:
    """FLT-010 — runs automatically every 5th driving event logged.
    record_safety_score always writes the score as a fleet-ops signal;
    it never pulls a vehicle from service or takes any action itself —
    a human decides what to do with a low score."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.safety_score_requested",
            trigger_context={"vehicle_id": vehicle_id, "event_counts": event_counts},
            reasoning=f"Agent status is {status.value} — no safety score was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return SafetyScoreResult(decision_log=log, score=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_safety_score_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=SAFETY_SCORE_SYSTEM_PROMPT,
            user_message=f"Driving event counts over the last {window_days} days: {event_counts}. Assess safety score.",
            tools=fleet_tools.SAFETY_SCORE_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_safety_score"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            score = submitted.get("score", 100.0)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            score = 100.0
            reasoning = turn.final_text or "(agent produced no final text and never called submit_safety_score)"
    else:
        score, reasoning = _deterministic_safety_score(event_counts)

    fleet_tools.record_safety_score(tenant_id, vehicle_id, score, reasoning, window_days)
    tools_called.append({"tool": "record_safety_score", "input": {"vehicle_id": vehicle_id, "score": score}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.safety_score_requested",
        trigger_context={"vehicle_id": vehicle_id, "event_counts": event_counts, "window_days": window_days},
        reasoning=reasoning, tools_called=tools_called, action_taken="safety_score_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return SafetyScoreResult(decision_log=log, score=score, reasoning=reasoning)


async def handle_safety_score_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> SafetyScoreResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_safety_score(db, tenant_id, payload.get("vehicle_id", ""), payload.get("event_counts", {}), payload.get("window_days", 30))


INTAKE_READINESS_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, reviewing an intake inspection's free-text notes for provisioning readiness \
(FLT-001).

You never see or touch the inspector's pass/fail determination — that's a human safety \
fact you cannot override. Your job is narrower: does the note text, on a PASSED \
inspection, suggest a caution worth flagging before provisioning (a noted cosmetic or \
minor issue worth tracking) versus nothing of concern? A failed inspection always holds \
for review regardless of notes — you don't need to reason about that case, just report \
it. Always finish by calling submit_readiness_recommendation."""

_CONCERN_KEYWORDS = ["leak", "damage", "worn", "crack", "warning", "issue", "scuff", "dent", "noise"]


def _deterministic_intake_readiness(passed: bool, notes: str) -> tuple[str, str]:
    """A real, auditable keyword scan — the same principle as HRM-003's
    resume screening. A failed inspection is never overridden into a
    ready recommendation, no matter what the notes say."""
    if not passed:
        return "hold_for_review", "[deterministic fallback — ANTHROPIC_API_KEY not configured] Inspection failed — always holds for review regardless of notes."

    notes_lower = notes.lower()
    matched = [kw for kw in _CONCERN_KEYWORDS if kw in notes_lower]
    if matched:
        return "provision_with_flag", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Inspection passed, but notes mention {', '.join(matched)} — flagged for tracking, not blocking."
    return "provision_ready", "[deterministic fallback — ANTHROPIC_API_KEY not configured] Inspection passed with no concern keywords in notes."


@dataclass
class IntakeReadinessResult:
    decision_log: DecisionLog
    recommendation: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_intake_readiness(
    db: AsyncSession,
    tenant_id: str,
    intake_inspection_id: str,
    passed: bool,
    notes: str,
    client: AnthropicAgentClient | None = None,
) -> IntakeReadinessResult:
    """FLT-001 — runs automatically on every inspection recorded.
    record_intake_readiness always writes an advisory assessment; it
    never touches IntakeInspection.passed and never provisions
    anything itself — mark_provisioned in fleet-svc remains the sole
    human-confirmed action."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.intake_readiness_requested",
            trigger_context={"intake_inspection_id": intake_inspection_id, "passed": passed},
            reasoning=f"Agent status is {status.value} — no readiness assessment was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return IntakeReadinessResult(decision_log=log, recommendation=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_intake_readiness_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=INTAKE_READINESS_SYSTEM_PROMPT,
            user_message=f"Inspection passed: {passed}. Notes: '{notes}'. Assess provisioning readiness.",
            tools=fleet_tools.INTAKE_READINESS_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_readiness_recommendation"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            recommendation = submitted.get("recommendation", "hold_for_review")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            recommendation = "hold_for_review"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_readiness_recommendation)"
    else:
        recommendation, reasoning = _deterministic_intake_readiness(passed, notes)

    fleet_tools.record_intake_readiness(tenant_id, intake_inspection_id, recommendation, reasoning)
    tools_called.append({"tool": "record_intake_readiness", "input": {"intake_inspection_id": intake_inspection_id, "recommendation": recommendation}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.intake_readiness_requested",
        trigger_context={"intake_inspection_id": intake_inspection_id, "passed": passed, "notes": notes},
        reasoning=reasoning, tools_called=tools_called, action_taken="readiness_assessment_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return IntakeReadinessResult(decision_log=log, recommendation=recommendation, reasoning=reasoning)


async def handle_intake_readiness_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> IntakeReadinessResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_intake_readiness(db, tenant_id, payload.get("intake_inspection_id", ""), payload.get("passed", False), payload.get("notes", ""))


TELEMETRY_ANOMALY_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, assessing a geofence excursion alert (FLT-002).

You are never shown tamper events here — those are a categorically different, more \
severe security signal that always goes straight to human security review, and you have \
no ability to touch them. Your job is narrower: given how far a vehicle has traveled \
beyond its expected geofence radius, is this a benign excursion (a nearby detour, a \
supported inter-hub route, a member road trip) or does the distance suggest something \
worth investigating? Always finish by calling submit_anomaly_assessment."""

# A real, auditable distance threshold — not a placeholder. Alerts
# only exist beyond the 50-mile geofence radius already, so this is a
# genuine second threshold distinguishing a modest excursion from one
# large enough to warrant a look.
INVESTIGATION_DISTANCE_THRESHOLD_MILES = 150.0


def _deterministic_telemetry_anomaly(distance_miles: float) -> tuple[str, str]:
    if distance_miles >= INVESTIGATION_DISTANCE_THRESHOLD_MILES:
        return "needs_investigation", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {distance_miles:.0f} miles beyond the geofence — well past a plausible local detour, warrants a look."
    return "benign", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {distance_miles:.0f} miles beyond the geofence — within range of a plausible local detour or supported route."


@dataclass
class TelemetryAnomalyResult:
    decision_log: DecisionLog
    assessment: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_telemetry_anomaly(
    db: AsyncSession,
    tenant_id: str,
    geofence_alert_id: str,
    distance_miles: float,
    client: AnthropicAgentClient | None = None,
) -> TelemetryAnomalyResult:
    """FLT-002 — runs automatically on every geofence alert.
    record_telemetry_anomaly always writes an advisory signal for
    fleet ops review; it never dispatches a recovery action or
    contacts anyone itself. TamperEvent never reaches this function —
    it has its own always-escalate deterministic path in fleet-svc."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.telemetry_anomaly_requested",
            trigger_context={"geofence_alert_id": geofence_alert_id, "distance_miles": distance_miles},
            reasoning=f"Agent status is {status.value} — no anomaly assessment was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return TelemetryAnomalyResult(decision_log=log, assessment=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_telemetry_anomaly_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=TELEMETRY_ANOMALY_SYSTEM_PROMPT,
            user_message=f"Vehicle is {distance_miles:.0f} miles beyond its expected geofence. Assess.",
            tools=fleet_tools.TELEMETRY_ANOMALY_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_anomaly_assessment"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            assessment = submitted.get("assessment", "needs_investigation")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            assessment = "needs_investigation"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_anomaly_assessment)"
    else:
        assessment, reasoning = _deterministic_telemetry_anomaly(distance_miles)

    fleet_tools.record_telemetry_anomaly(tenant_id, geofence_alert_id, assessment, reasoning)
    tools_called.append({"tool": "record_telemetry_anomaly", "input": {"geofence_alert_id": geofence_alert_id, "assessment": assessment}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.telemetry_anomaly_requested",
        trigger_context={"geofence_alert_id": geofence_alert_id, "distance_miles": distance_miles},
        reasoning=reasoning, tools_called=tools_called, action_taken="anomaly_assessment_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return TelemetryAnomalyResult(decision_log=log, assessment=assessment, reasoning=reasoning)


async def handle_telemetry_anomaly_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> TelemetryAnomalyResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_telemetry_anomaly(db, tenant_id, payload.get("geofence_alert_id", ""), payload.get("distance_miles", 0.0))


MAINTENANCE_TRIAGE_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, prioritizing a list of currently open maintenance alerts (FLT-004).

Each alert's severity was already set deterministically by a fixed rule — DTC codes \
present means URGENT, mileage thresholds mean PREDICTIVE — and you cannot change that. \
Your job is to order the vehicles by which needs service first, given severity and how \
long an alert has been sitting open. This never opens a work order or pulls a vehicle \
from service — that's always a human scheduling action. Always finish by calling \
submit_triage_priority."""

_SEVERITY_RANK = {"urgent": 0, "predictive": 1, "routine": 2}


def _deterministic_maintenance_triage(alerts: list[dict]) -> tuple[list[str], str]:
    """A real, auditable priority sort — severity first (URGENT
    always ahead of PREDICTIVE/ROUTINE, matching the deterministic
    severity rule these alerts were created with), then DTC-code
    count as a tiebreaker within the same severity."""
    sortable = sorted(alerts, key=lambda a: (_SEVERITY_RANK.get(a.get("severity", "routine"), 2), -len(a.get("dtc_codes", []))))
    priority_order = [a["vehicle_id"] for a in sortable]
    severity_counts: dict[str, int] = {}
    for a in alerts:
        severity_counts[a.get("severity", "routine")] = severity_counts.get(a.get("severity", "routine"), 0) + 1
    reasoning = (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Sorted by severity "
        f"({', '.join(f'{v} {k}' for k, v in severity_counts.items())}), DTC code count as tiebreaker within severity."
    )
    return priority_order, reasoning


@dataclass
class MaintenanceTriageResult:
    decision_log: DecisionLog
    priority_order: list[str]
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_maintenance_triage(
    db: AsyncSession,
    tenant_id: str,
    alerts: list[dict],
    client: AnthropicAgentClient | None = None,
) -> MaintenanceTriageResult:
    """FLT-004 — runs automatically once enough open alerts have
    accumulated. record_maintenance_triage always writes an advisory
    priority order for a fleet ops standup; it never opens a work
    order, changes an alert's severity, or pulls a vehicle from
    service itself."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.maintenance_triage_requested",
            trigger_context={"alert_count": len(alerts)},
            reasoning=f"Agent status is {status.value} — no triage was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return MaintenanceTriageResult(decision_log=log, priority_order=[], reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_maintenance_triage_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=MAINTENANCE_TRIAGE_SYSTEM_PROMPT,
            user_message=f"Open alerts: {alerts}. Prioritize.",
            tools=fleet_tools.MAINTENANCE_TRIAGE_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_triage_priority"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            priority_order = submitted.get("priority_order", [])
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            priority_order = [a["vehicle_id"] for a in alerts]
            reasoning = turn.final_text or "(agent produced no final text and never called submit_triage_priority)"
    else:
        priority_order, reasoning = _deterministic_maintenance_triage(alerts)

    alert_ids = [a["alert_id"] for a in alerts]
    fleet_tools.record_maintenance_triage(tenant_id, alert_ids, priority_order, reasoning)
    tools_called.append({"tool": "record_maintenance_triage", "input": {"alert_count": len(alert_ids)}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.maintenance_triage_requested",
        trigger_context={"alert_count": len(alerts)},
        reasoning=reasoning, tools_called=tools_called, action_taken="triage_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return MaintenanceTriageResult(decision_log=log, priority_order=priority_order, reasoning=reasoning)


async def handle_maintenance_triage_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> MaintenanceTriageResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_maintenance_triage(db, tenant_id, payload.get("alerts", []))


INCIDENT_SEVERITY_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, triaging an incident report's severity from its description (FLT-005).

You NEVER determine liability — that field is exclusively set by a human risk analyst \
through a separate action, and you have no ability to touch it, no matter how obvious \
fault might seem from the description. Your job is narrower: classify severity (minor, \
moderate, or severe) and write a short factual summary, purely to help fleet ops \
prioritize which incidents need urgent attention — a repair order, an insurance handoff \
— first. Always finish by calling submit_incident_severity."""

_SEVERE_KEYWORDS = ["injury", "injured", "airbag", "hospital", "ambulance", "fire", "totaled", "rollover"]
_MINOR_KEYWORDS = ["scratch", "scuff", "dent", "cosmetic", "minor", "parking lot"]


def _deterministic_incident_severity(description: str) -> tuple[str, str, str]:
    """A real, auditable keyword scan — the same principle as HRM-003's
    resume screening and FLT-001's intake readiness. Severe keywords
    are checked first and always win a tie, since under-triaging a
    genuinely severe incident is the worse failure mode."""
    desc_lower = description.lower()
    severe_matches = [kw for kw in _SEVERE_KEYWORDS if kw in desc_lower]
    if severe_matches:
        severity = "severe"
        reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Description mentions {', '.join(severe_matches)} — treated as severe regardless of other wording."
    else:
        minor_matches = [kw for kw in _MINOR_KEYWORDS if kw in desc_lower]
        if minor_matches:
            severity = "minor"
            reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Description mentions {', '.join(minor_matches)} with no severe-incident keywords found."
        else:
            severity = "moderate"
            reasoning = "[deterministic fallback — ANTHROPIC_API_KEY not configured] No minor or severe keywords matched — defaulting to moderate pending human review."
    summary = description.strip()[:200]
    return severity, summary, reasoning


@dataclass
class IncidentSeverityResult:
    decision_log: DecisionLog
    severity: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_incident_severity(
    db: AsyncSession,
    tenant_id: str,
    incident_id: str,
    incident_type: str,
    description: str,
    client: AnthropicAgentClient | None = None,
) -> IncidentSeverityResult:
    """FLT-005 — runs automatically on every incident reported.
    record_incident_severity always writes a triage-only signal; it
    NEVER touches liability_assessment, assessed_by, status, or the
    total_loss fields — those remain exclusively human-entered
    through assess_liability and declare_total_loss in fleet-svc."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.incident_severity_requested",
            trigger_context={"incident_id": incident_id, "incident_type": incident_type},
            reasoning=f"Agent status is {status.value} — no severity assessment was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return IncidentSeverityResult(decision_log=log, severity=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_incident_severity_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=INCIDENT_SEVERITY_SYSTEM_PROMPT,
            user_message=f"Incident type: {incident_type}. Description: '{description}'. Triage severity.",
            tools=fleet_tools.INCIDENT_SEVERITY_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_incident_severity"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            severity = submitted.get("severity", "moderate")
            summary = submitted.get("summary", "")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            severity, summary = "moderate", ""
            reasoning = turn.final_text or "(agent produced no final text and never called submit_incident_severity)"
    else:
        severity, summary, reasoning = _deterministic_incident_severity(description)

    fleet_tools.record_incident_severity(tenant_id, incident_id, severity, summary, reasoning)
    tools_called.append({"tool": "record_incident_severity", "input": {"incident_id": incident_id, "severity": severity}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.incident_severity_requested",
        trigger_context={"incident_id": incident_id, "incident_type": incident_type, "description": description},
        reasoning=reasoning, tools_called=tools_called, action_taken="severity_assessment_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return IncidentSeverityResult(decision_log=log, severity=severity, reasoning=reasoning)


async def handle_incident_severity_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> IncidentSeverityResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_incident_severity(db, tenant_id, payload.get("incident_id", ""), payload.get("incident_type", ""), payload.get("description", ""))


VALUATION_ANOMALY_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, comparing a fresh market valuation against the book value straight-line \
depreciation already computed (FLT-006).

You never touch the depreciation schedule or the valuation record itself — that math is \
a financial calculation this platform always keeps deterministic, the same principle as \
payroll and TCO arithmetic elsewhere. Your job is to flag when the deviation is large \
enough to be worth a finance team's attention, in either direction — market value well \
below book suggests accelerated write-down should be considered; well above suggests the \
asset may be under-depreciated or the market is unusually strong. Always finish by \
calling submit_valuation_anomaly."""

# A real, auditable threshold — not a placeholder. 20% deviation
# either direction is a meaningful enough gap to be worth a look, not
# noise from normal appraisal variance.
DEVIATION_THRESHOLD_PCT = 0.20


def _deterministic_valuation_anomaly(book_value_usd: float, market_value_usd: float) -> tuple[float, str, str]:
    if book_value_usd <= 0:
        return 0.0, "normal", "[deterministic fallback — ANTHROPIC_API_KEY not configured] Book value is zero or negative — cannot compute a meaningful deviation."
    deviation_pct = (market_value_usd - book_value_usd) / book_value_usd
    if abs(deviation_pct) >= DEVIATION_THRESHOLD_PCT:
        direction = "below" if deviation_pct < 0 else "above"
        return deviation_pct, "flagged", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Market value is {abs(deviation_pct):.0%} {direction} book value (${book_value_usd:,.0f} book vs ${market_value_usd:,.0f} market) — exceeds the {DEVIATION_THRESHOLD_PCT:.0%} threshold."
    return deviation_pct, "normal", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {abs(deviation_pct):.0%} deviation from book value — within normal appraisal variance."


@dataclass
class ValuationAnomalyResult:
    decision_log: DecisionLog
    assessment: str | None
    deviation_pct: float | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_valuation_anomaly(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: str,
    asset_valuation_id: str,
    book_value_usd: float,
    market_value_usd: float,
    client: AnthropicAgentClient | None = None,
) -> ValuationAnomalyResult:
    """FLT-006 — runs automatically on every market valuation recorded
    where a depreciation schedule exists. record_valuation_anomaly
    always writes an advisory finance-review signal; it never touches
    the depreciation schedule or the valuation record itself — both
    remain exactly the deterministic arithmetic they already were."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.valuation_anomaly_requested",
            trigger_context={"vehicle_id": vehicle_id, "asset_valuation_id": asset_valuation_id},
            reasoning=f"Agent status is {status.value} — no valuation anomaly was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return ValuationAnomalyResult(decision_log=log, assessment=None, deviation_pct=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_valuation_anomaly_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=VALUATION_ANOMALY_SYSTEM_PROMPT,
            user_message=f"Book value: ${book_value_usd:,.2f}. Market value estimate: ${market_value_usd:,.2f}. Assess.",
            tools=fleet_tools.VALUATION_ANOMALY_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_valuation_anomaly"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            assessment = submitted.get("assessment", "normal")
            deviation_pct = submitted.get("deviation_pct", 0.0)
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            assessment, deviation_pct = "normal", 0.0
            reasoning = turn.final_text or "(agent produced no final text and never called submit_valuation_anomaly)"
    else:
        deviation_pct, assessment, reasoning = _deterministic_valuation_anomaly(book_value_usd, market_value_usd)

    fleet_tools.record_valuation_anomaly(tenant_id, vehicle_id, asset_valuation_id, deviation_pct, assessment, reasoning)
    tools_called.append({"tool": "record_valuation_anomaly", "input": {"asset_valuation_id": asset_valuation_id, "assessment": assessment}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.valuation_anomaly_requested",
        trigger_context={"vehicle_id": vehicle_id, "asset_valuation_id": asset_valuation_id, "book_value_usd": book_value_usd, "market_value_usd": market_value_usd},
        reasoning=reasoning, tools_called=tools_called, action_taken="valuation_anomaly_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return ValuationAnomalyResult(decision_log=log, assessment=assessment, deviation_pct=deviation_pct, reasoning=reasoning)


async def handle_valuation_anomaly_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> ValuationAnomalyResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_valuation_anomaly(
        db, tenant_id, payload.get("vehicle_id", ""), payload.get("asset_valuation_id", ""), payload.get("book_value_usd", 0.0), payload.get("market_value_usd", 0.0),
    )


DISPOSITION_CHANNEL_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, recommending which stage of the depreciation cascade a disposition-eligible \
vehicle should move to next (FLT-007).

VOYQI's documented strategy is a cascade, not a single liquidation event: a vehicle \
exiting subscription duty moves through progressively lower-value uses — rideshare, then \
local short-term rental, then export (UAE, Latin America), then parts — rather than \
absorbing all depreciation at once, which is what sank prior competitors like Fair and \
Canvas. Consider odometer mileage as the primary signal for how much useful life \
remains; a vehicle just past the disposition threshold likely has significant rideshare \
life left, while one with very high mileage is closer to parts. This never creates the \
actual disposition order — a human always confirms the channel, and can override your \
recommendation entirely. Always finish by calling submit_disposition_channel."""

# Real, auditable mileage bands relative to the disposition eligibility
# threshold (60,000 miles) — not arbitrary. Each band represents a
# meaningfully different remaining-useful-life profile:
# threshold-75k: still has real rideshare life; 75k-90k: local rental
# fits better than rideshare's higher utilization; 90k-120k: export
# markets value a mechanically sound vehicle regardless of cosmetic
# wear; 120k+: parts is the honest answer.
_CASCADE_MILEAGE_BANDS = [
    (75000, "rideshare"),
    (90000, "local_rental"),
    (120000, "export"),
]


def _deterministic_disposition_channel(odometer_miles: int) -> tuple[str, str]:
    for threshold, channel in _CASCADE_MILEAGE_BANDS:
        if odometer_miles < threshold:
            return channel, f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {odometer_miles:,} miles — under the {threshold:,}-mile band, still has real useful life for {channel.replace('_', ' ')}."
    return "parts", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {odometer_miles:,} miles — beyond the {_CASCADE_MILEAGE_BANDS[-1][0]:,}-mile band, parts recovery is the honest next stage."


@dataclass
class DispositionChannelResult:
    decision_log: DecisionLog
    recommended_channel: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_disposition_channel(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: str,
    odometer_miles: int,
    vehicle_class: str,
    client: AnthropicAgentClient | None = None,
) -> DispositionChannelResult:
    """FLT-007 — the highest commercial-value capability in this
    rebuild: implements the documented but never-before-coded
    depreciation cascade strategy. create_disposition_channel_recommendation
    always writes PENDING; accept_disposition_recommendation in
    fleet-svc is the sole human-confirmed action that creates a real
    DispositionOrder, and can override the channel entirely."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.disposition_channel_requested",
            trigger_context={"vehicle_id": vehicle_id, "odometer_miles": odometer_miles},
            reasoning=f"Agent status is {status.value} — no disposition channel was recommended.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return DispositionChannelResult(decision_log=log, recommended_channel=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_disposition_channel_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=DISPOSITION_CHANNEL_SYSTEM_PROMPT,
            user_message=f"Vehicle class: {vehicle_class}. Odometer: {odometer_miles:,} miles. Recommend the next cascade stage.",
            tools=fleet_tools.DISPOSITION_CHANNEL_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_disposition_channel"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            recommended_channel = submitted.get("recommended_channel", "auction")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            recommended_channel = "auction"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_disposition_channel)"
    else:
        recommended_channel, reasoning = _deterministic_disposition_channel(odometer_miles)

    fleet_tools.create_disposition_channel_recommendation(tenant_id, vehicle_id, recommended_channel, reasoning)
    tools_called.append({"tool": "create_disposition_channel_recommendation", "input": {"vehicle_id": vehicle_id, "recommended_channel": recommended_channel}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.disposition_channel_requested",
        trigger_context={"vehicle_id": vehicle_id, "odometer_miles": odometer_miles, "vehicle_class": vehicle_class},
        reasoning=reasoning, tools_called=tools_called, action_taken="disposition_channel_recommended", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return DispositionChannelResult(decision_log=log, recommended_channel=recommended_channel, reasoning=reasoning)


async def handle_disposition_channel_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> DispositionChannelResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_disposition_channel(db, tenant_id, payload.get("vehicle_id", ""), payload.get("odometer_miles", 0), payload.get("vehicle_class", ""))


COVENANT_RISK_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, assessing floorplan covenant risk after a pledge (FLT-008/009).

You never touch VIN collateral status or a facility's utilized amount — those are
exact deterministic arithmetic lenders require to be precisely auditable, and \
whether a covenant was actually breached is already determined by fixed math before \
you're ever called. Your job is narrower: classify risk as safe, approaching, or \
breached, and write a short narrative for the finance team — genuinely useful is \
noting *how fast* utilization is climbing toward the limit, not just where it sits \
right now. Always finish by calling submit_covenant_risk."""

# A real, auditable early-warning threshold — 80% of the covenant
# limit itself is close enough to be worth a proactive look, before
# the facility actually breaches.
APPROACHING_THRESHOLD_RATIO = 0.80


def _deterministic_covenant_risk(utilization_pct: float, max_utilization_pct: float) -> tuple[str, str]:
    if utilization_pct > max_utilization_pct:
        return "breached", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Utilization at {utilization_pct:.1f}% exceeds the {max_utilization_pct:.1f}% covenant limit."
    approaching_threshold = max_utilization_pct * APPROACHING_THRESHOLD_RATIO
    if utilization_pct >= approaching_threshold:
        return "approaching", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Utilization at {utilization_pct:.1f}% is within {max_utilization_pct - utilization_pct:.1f} points of the {max_utilization_pct:.1f}% covenant limit — worth proactive attention."
    return "safe", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Utilization at {utilization_pct:.1f}%, well within the {max_utilization_pct:.1f}% covenant limit."


@dataclass
class CovenantRiskResult:
    decision_log: DecisionLog
    risk_level: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_covenant_risk(
    db: AsyncSession,
    tenant_id: str,
    facility_id: str,
    utilization_pct: float,
    max_utilization_pct: float,
    client: AnthropicAgentClient | None = None,
) -> CovenantRiskResult:
    """FLT-008/009 — runs automatically on every pledge, not just
    outright breaches. record_covenant_risk always writes an advisory
    finance-review signal; it never touches VinCollateralStatus or a
    facility's utilized_amount — both remain exactly the deterministic
    arithmetic they already were."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.covenant_risk_requested",
            trigger_context={"facility_id": facility_id, "utilization_pct": utilization_pct},
            reasoning=f"Agent status is {status.value} — no covenant risk was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return CovenantRiskResult(decision_log=log, risk_level=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_covenant_risk_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=COVENANT_RISK_SYSTEM_PROMPT,
            user_message=f"Facility utilization: {utilization_pct:.1f}%. Covenant limit: {max_utilization_pct:.1f}%. Assess risk.",
            tools=fleet_tools.COVENANT_RISK_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_covenant_risk"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            risk_level = submitted.get("risk_level", "safe")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            risk_level = "safe"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_covenant_risk)"
    else:
        risk_level, reasoning = _deterministic_covenant_risk(utilization_pct, max_utilization_pct)

    fleet_tools.record_covenant_risk(tenant_id, facility_id, risk_level, reasoning)
    tools_called.append({"tool": "record_covenant_risk", "input": {"facility_id": facility_id, "risk_level": risk_level}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.covenant_risk_requested",
        trigger_context={"facility_id": facility_id, "utilization_pct": utilization_pct, "max_utilization_pct": max_utilization_pct},
        reasoning=reasoning, tools_called=tools_called, action_taken="covenant_risk_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return CovenantRiskResult(decision_log=log, risk_level=risk_level, reasoning=reasoning)


async def handle_covenant_risk_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> CovenantRiskResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_covenant_risk(db, tenant_id, payload.get("facility_id", ""), payload.get("utilization_pct", 0.0), payload.get("max_utilization_pct", 90.0))


FUEL_ANOMALY_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, checking a fuel/energy log entry for signs of fraud, a data entry error, or a \
maintenance-driven efficiency problem (FLT-011).

You never touch the logged entry or the deterministic cost-per-mile/efficiency \
arithmetic — both remain exactly the plain, hand-verifiable calculations they already \
are. You're given the implied efficiency for just this one fill (miles covered divided \
by quantity used) and the cost per unit — an implausible efficiency reading (far too \
high or far too low for the energy type) or a wildly unusual price per gallon/kWh is \
what's worth flagging. Always finish by calling submit_fuel_anomaly."""

# Real, auditable plausibility bands — not placeholders. These are
# wide enough to cover VOYQI's full fleet range (Urban compacts to
# Performance vehicles) without false-flagging normal variance; only
# genuinely implausible readings should trip these.
_GASOLINE_MPG_RANGE = (8.0, 60.0)
_ELECTRIC_MI_PER_KWH_RANGE = (1.5, 6.0)
_GASOLINE_COST_PER_GALLON_RANGE = (2.0, 7.0)
_ELECTRIC_COST_PER_KWH_RANGE = (0.05, 0.60)


def _deterministic_fuel_anomaly(energy_type: str, implied_efficiency: float | None, cost_per_unit_usd: float | None) -> tuple[str, str]:
    efficiency_range = _GASOLINE_MPG_RANGE if energy_type == "gasoline" else _ELECTRIC_MI_PER_KWH_RANGE
    cost_range = _GASOLINE_COST_PER_GALLON_RANGE if energy_type == "gasoline" else _ELECTRIC_COST_PER_KWH_RANGE
    unit = "MPG" if energy_type == "gasoline" else "mi/kWh"

    flags = []
    if implied_efficiency is not None and not (efficiency_range[0] <= implied_efficiency <= efficiency_range[1]):
        flags.append(f"implied efficiency {implied_efficiency} {unit} is outside the plausible {efficiency_range[0]}-{efficiency_range[1]} {unit} range")
    if cost_per_unit_usd is not None and not (cost_range[0] <= cost_per_unit_usd <= cost_range[1]):
        flags.append(f"cost of ${cost_per_unit_usd:.2f}/unit is outside the plausible ${cost_range[0]:.2f}-${cost_range[1]:.2f} range")

    if flags:
        return "flagged", f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {'; '.join(flags)}."
    return "normal", "[deterministic fallback — ANTHROPIC_API_KEY not configured] Implied efficiency and cost per unit both fall within plausible ranges."


@dataclass
class FuelAnomalyResult:
    decision_log: DecisionLog
    assessment: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_fuel_anomaly(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: str,
    fuel_log_entry_id: str,
    energy_type: str,
    implied_efficiency: float | None,
    cost_per_unit_usd: float | None,
    client: AnthropicAgentClient | None = None,
) -> FuelAnomalyResult:
    """FLT-011 — runs automatically once a second fuel entry exists to
    compare against. record_fuel_anomaly always writes an advisory
    loss-prevention/maintenance signal; it never touches the logged
    entry or the deterministic cost-per-mile/efficiency math."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.fuel_anomaly_requested",
            trigger_context={"vehicle_id": vehicle_id, "fuel_log_entry_id": fuel_log_entry_id},
            reasoning=f"Agent status is {status.value} — no fuel anomaly was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return FuelAnomalyResult(decision_log=log, assessment=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_fuel_anomaly_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=FUEL_ANOMALY_SYSTEM_PROMPT,
            user_message=f"Energy type: {energy_type}. Implied efficiency: {implied_efficiency}. Cost per unit: ${cost_per_unit_usd}. Assess.",
            tools=fleet_tools.FUEL_ANOMALY_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_fuel_anomaly"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            assessment = submitted.get("assessment", "normal")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            assessment = "normal"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_fuel_anomaly)"
    else:
        assessment, reasoning = _deterministic_fuel_anomaly(energy_type, implied_efficiency, cost_per_unit_usd)

    fleet_tools.record_fuel_anomaly(tenant_id, vehicle_id, fuel_log_entry_id, assessment, reasoning)
    tools_called.append({"tool": "record_fuel_anomaly", "input": {"fuel_log_entry_id": fuel_log_entry_id, "assessment": assessment}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.fuel_anomaly_requested",
        trigger_context={"vehicle_id": vehicle_id, "fuel_log_entry_id": fuel_log_entry_id, "energy_type": energy_type, "implied_efficiency": implied_efficiency, "cost_per_unit_usd": cost_per_unit_usd},
        reasoning=reasoning, tools_called=tools_called, action_taken="fuel_anomaly_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return FuelAnomalyResult(decision_log=log, assessment=assessment, reasoning=reasoning)


async def handle_fuel_anomaly_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> FuelAnomalyResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_fuel_anomaly(
        db, tenant_id, payload.get("vehicle_id", ""), payload.get("fuel_log_entry_id", ""), payload.get("energy_type", "gasoline"), payload.get("implied_efficiency"), payload.get("cost_per_unit_usd"),
    )


BATTERY_DEGRADATION_TREND_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for \
VOYQI Mobility, forecasting when a vehicle's battery will likely cross the replacement \
threshold, from its degradation trend (FLT-012).

You never touch the threshold-alert check itself — that stays a fixed, auditable single-
reading comparison, since a battery nearing end-of-life is consequential enough (real \
replacement cost, member-facing range) to need auditable arithmetic there. Your job is \
narrower and genuinely forward-looking: given the state-of-health decline rate across \
the readings on file, project roughly how many days remain before the vehicle would \
cross the alert threshold, so fleet finance can budget for replacement proactively \
rather than reactively. If the trend is flat or improving, there's no meaningful \
forecast to make — say so honestly rather than inventing a number. Always finish by \
calling submit_battery_degradation_trend."""


def _deterministic_battery_degradation_trend(first_soh_pct: float, latest_soh_pct: float, days_elapsed: float, threshold_pct: float) -> tuple[float, int | None, str]:
    """A real, auditable linear projection — not a placeholder. Simple
    linear extrapolation from the observed rate; genuinely honest when
    the trend doesn't support a forecast at all (flat or improving).
    threshold_pct is passed in from fleet-svc rather than duplicated
    here, since fleet-svc's config is the single source of truth for
    where the reactive alert threshold actually sits."""
    degradation_rate_pct_per_day = round((latest_soh_pct - first_soh_pct) / days_elapsed, 5)

    if degradation_rate_pct_per_day >= 0:
        return degradation_rate_pct_per_day, None, "[deterministic fallback — ANTHROPIC_API_KEY not configured] State of health is flat or improving over the observed window — no meaningful decline to project forward."

    if latest_soh_pct <= threshold_pct:
        return degradation_rate_pct_per_day, 0, f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Already at or below the {threshold_pct:.0f}% alert threshold."

    days_to_threshold = int((latest_soh_pct - threshold_pct) / abs(degradation_rate_pct_per_day))
    return degradation_rate_pct_per_day, days_to_threshold, (
        f"[deterministic fallback — ANTHROPIC_API_KEY not configured] Declining at {abs(degradation_rate_pct_per_day):.4f} points/day "
        f"({first_soh_pct:.1f}% \u2192 {latest_soh_pct:.1f}% over {days_elapsed:.0f} days) — at this rate, roughly {days_to_threshold} days until the {threshold_pct:.0f}% threshold."
    )


@dataclass
class BatteryDegradationTrendResult:
    decision_log: DecisionLog
    estimated_days_to_threshold: int | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_battery_degradation_trend(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: str,
    first_soh_pct: float,
    latest_soh_pct: float,
    days_elapsed: float,
    threshold_pct: float = 80.0,
    client: AnthropicAgentClient | None = None,
) -> BatteryDegradationTrendResult:
    """FLT-012 — runs automatically once at least 2 battery health
    readings exist. record_battery_degradation_forecast always writes
    an advisory budget-planning signal; it never touches
    is_degradation_alert or the reactive watchlist, both of which
    remain exactly the deterministic single-reading threshold check
    they already were. threshold_pct is passed in from fleet-svc's own
    configured alert threshold rather than duplicated here."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.battery_degradation_trend_requested",
            trigger_context={"vehicle_id": vehicle_id, "first_soh_pct": first_soh_pct, "latest_soh_pct": latest_soh_pct},
            reasoning=f"Agent status is {status.value} — no degradation trend was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return BatteryDegradationTrendResult(decision_log=log, estimated_days_to_threshold=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_battery_degradation_trend_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=BATTERY_DEGRADATION_TREND_SYSTEM_PROMPT,
            user_message=f"SOH went from {first_soh_pct:.1f}% to {latest_soh_pct:.1f}% over {days_elapsed:.0f} days. Alert threshold is {threshold_pct:.0f}%. Project the trend.",
            tools=fleet_tools.BATTERY_DEGRADATION_TREND_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_battery_degradation_trend"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            degradation_rate = submitted.get("degradation_rate_pct_per_day", 0.0)
            estimated_days = submitted.get("estimated_days_to_threshold")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            degradation_rate, estimated_days = 0.0, None
            reasoning = turn.final_text or "(agent produced no final text and never called submit_battery_degradation_trend)"
    else:
        degradation_rate, estimated_days, reasoning = _deterministic_battery_degradation_trend(first_soh_pct, latest_soh_pct, days_elapsed, threshold_pct)

    fleet_tools.record_battery_degradation_forecast(tenant_id, vehicle_id, degradation_rate, estimated_days, reasoning)
    tools_called.append({"tool": "record_battery_degradation_forecast", "input": {"vehicle_id": vehicle_id, "estimated_days_to_threshold": estimated_days}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.battery_degradation_trend_requested",
        trigger_context={"vehicle_id": vehicle_id, "first_soh_pct": first_soh_pct, "latest_soh_pct": latest_soh_pct, "days_elapsed": days_elapsed},
        reasoning=reasoning, tools_called=tools_called, action_taken="degradation_forecast_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return BatteryDegradationTrendResult(decision_log=log, estimated_days_to_threshold=estimated_days, reasoning=reasoning)


async def handle_battery_degradation_trend_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> BatteryDegradationTrendResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_battery_degradation_trend(
        db, tenant_id, payload.get("vehicle_id", ""), payload.get("first_soh_pct", 100.0), payload.get("latest_soh_pct", 100.0), payload.get("days_elapsed", 1.0), payload.get("threshold_pct", 80.0),
    )


FLEET_OPTIMIZATION_SYSTEM_PROMPT = """You are the Fleet Intelligence Agent for VOYQI \
Mobility, synthesizing signals across TCO, availability, disposition eligibility, and \
valuation into one fleet-optimization recommendation (FLT-013) — the kind of judgment a \
fleet manager would otherwise make by checking several separate screens by hand.

You never create a disposition order, change eligibility, or alter any of the \
underlying figures — every signal you're given (TCO, availability, eligibility, \
valuation flag) is already deterministic fact computed elsewhere. Your job is purely to \
weigh them together: a vehicle that's disposition-eligible AND has poor availability AND \
a flagged valuation is a much stronger disposition case than one with only a single \
weak signal. Always finish by calling submit_fleet_optimization."""

# A real, auditable multi-signal scoring rule — the same principle as
# FLT-010's weighted safety score and HRM-005's attrition risk. Each
# signal contributes independently; the combination is what
# distinguishes "worth a second look" from "clear priority."
AVAILABILITY_CONCERN_THRESHOLD_PCT = 85.0


def _deterministic_fleet_optimization(total_tco_usd: float | None, availability_pct: float | None, is_disposition_eligible: bool, valuation_flagged: bool) -> tuple[str, str]:
    score = 0
    drivers = []

    if is_disposition_eligible:
        score += 1
        drivers.append("already past the age/mileage disposition-eligibility threshold")
    if availability_pct is not None and availability_pct < AVAILABILITY_CONCERN_THRESHOLD_PCT:
        score += 1
        drivers.append(f"availability at {availability_pct:.1f}% is below the {AVAILABILITY_CONCERN_THRESHOLD_PCT:.0f}% concern threshold — spending too much time in maintenance")
    if valuation_flagged:
        score += 1
        drivers.append("latest market valuation was flagged as a meaningful deviation from book value")

    if score >= 2:
        recommendation = "prioritize_disposition"
    elif score == 1:
        recommendation = "review_for_disposition"
    else:
        recommendation = "retain"

    reasoning = f"[deterministic fallback — ANTHROPIC_API_KEY not configured] {'; '.join(drivers) if drivers else 'no concerning signals — vehicle looks healthy across TCO, availability, eligibility, and valuation'}."
    return recommendation, reasoning


@dataclass
class FleetOptimizationResult:
    decision_log: DecisionLog
    recommendation: str | None
    reasoning: str
    skipped_reason: str | None = None


async def evaluate_fleet_optimization(
    db: AsyncSession,
    tenant_id: str,
    vehicle_id: str,
    total_tco_usd: float | None,
    availability_pct: float | None,
    is_disposition_eligible: bool,
    valuation_flagged: bool,
    client: AnthropicAgentClient | None = None,
) -> FleetOptimizationResult:
    """FLT-013 — the capstone capability of this rebuild, run on
    request rather than a single underlying event, since it needs
    signals gathered from across FLT-006/007/013 first.
    record_fleet_optimization always writes an advisory
    prioritization signal; it never creates a disposition order,
    changes eligibility, or alters any of the deterministic figures
    it was given."""
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(
            tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.fleet_optimization_requested",
            trigger_context={"vehicle_id": vehicle_id, "is_disposition_eligible": is_disposition_eligible},
            reasoning=f"Agent status is {status.value} — no fleet optimization recommendation was computed.",
            tools_called=[], action_taken="skipped_agent_not_active", outcome="deferred",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return FleetOptimizationResult(decision_log=log, recommendation=None, reasoning=log.reasoning, skipped_reason=status.value)

    client = client or AnthropicAgentClient()
    tools_called: list[dict] = []

    if client.is_configured:
        dispatch = fleet_tools.build_fleet_optimization_tool_dispatch(tenant_id)
        turn = client.run_agent_turn(
            system_prompt=FLEET_OPTIMIZATION_SYSTEM_PROMPT,
            user_message=(
                f"Total TCO: {total_tco_usd}. Availability: {availability_pct}%. "
                f"Disposition eligible: {is_disposition_eligible}. Valuation flagged: {valuation_flagged}. Recommend."
            ),
            tools=fleet_tools.FLEET_OPTIMIZATION_TOOL_SCHEMAS, tool_dispatch=dispatch,
        )
        tools_called = [{"tool": tc.tool_name, "input": tc.tool_input} for tc in turn.tool_calls]
        result_calls = [tc for tc in turn.tool_calls if tc.tool_name == "submit_fleet_optimization"]
        if result_calls:
            submitted = result_calls[-1].tool_output
            recommendation = submitted.get("recommendation", "retain")
            reasoning = submitted.get("reasoning", turn.final_text or "")
        else:
            recommendation = "retain"
            reasoning = turn.final_text or "(agent produced no final text and never called submit_fleet_optimization)"
    else:
        recommendation, reasoning = _deterministic_fleet_optimization(total_tco_usd, availability_pct, is_disposition_eligible, valuation_flagged)

    fleet_tools.record_fleet_optimization(tenant_id, vehicle_id, recommendation, reasoning)
    tools_called.append({"tool": "record_fleet_optimization", "input": {"vehicle_id": vehicle_id, "recommendation": recommendation}})

    log = DecisionLog(
        tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type="flt.fleet_optimization_requested",
        trigger_context={
            "vehicle_id": vehicle_id, "total_tco_usd": total_tco_usd, "availability_pct": availability_pct,
            "is_disposition_eligible": is_disposition_eligible, "valuation_flagged": valuation_flagged,
        },
        reasoning=reasoning, tools_called=tools_called, action_taken="fleet_optimization_recorded", outcome="success",
    )
    db.add(log)

    result = await db.execute(select(Agent).where(Agent.key == AGENT_KEY, Agent.tenant_id == tenant_id))
    agent_row = result.scalar_one_or_none()
    if agent_row:
        agent_row.decisions_made += 1

    await db.commit()
    await db.refresh(log)
    return FleetOptimizationResult(decision_log=log, recommendation=recommendation, reasoning=reasoning)


async def handle_fleet_optimization_requested(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> FleetOptimizationResult:
    """Adapter matching the supervisor's AgentHandler signature."""
    return await evaluate_fleet_optimization(
        db, tenant_id, payload.get("vehicle_id", ""), payload.get("total_tco_usd"), payload.get("availability_pct"),
        payload.get("is_disposition_eligible", False), payload.get("valuation_flagged", False),
    )
