"""
Churn risk sweep — the platform's first genuine scheduled batch job,
distinct from the event-driven batch consumer tier in kafka_consumer.py.
That tier reacts to events as they arrive; this module runs on a
schedule (a Kubernetes CronJob in production) and processes every
member in one bulk pass, which is what "batch" means in the fuller
sense: throughput-heavy, non-interactive work with no event to react to
because none of this is triggered by anything a member did just now.

Context assembly is honest about what's real and what isn't:

- days_since_last_use comes from fleet-svc's own latest-telemetry
  reading for the member's active vehicle — a real cross-service call,
  not a stand-in. A member with no active_vehicle_id, or one whose
  vehicle has never reported telemetry, gets no days-since-last-use
  signal at all rather than a fabricated number; evaluate_churn_risk's
  own deterministic scoring already treats a missing key as "no signal
  contributed," not "definitely fine."
- pause_count_last_year comes from subscription-svc's real
  PauseHistoryEntry table.
- support_complaints_last_90d has no home yet anywhere on this
  platform — no case-management or complaint-tracking system exists
  for subscription members (HRM has one for employees; that is a
  different domain and a different table). This context key is
  omitted rather than defaulted to 0, which would silently claim
  "definitely no complaints" instead of "we don't track this yet."
"""
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.services import fleet_tools, member_experience_agent, subscription_tools

logger = logging.getLogger("ai-orchestration-svc.batch.churn_sweep")


def _build_churn_context(tenant_id: str, member: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}

    vehicle_id = member.get("active_vehicle_id")
    if vehicle_id:
        try:
            reading = fleet_tools.get_latest_telemetry(tenant_id, vehicle_id)
        except httpx.HTTPError:
            logger.warning("fleet-svc unreachable fetching telemetry for vehicle %s — no days_since_last_use signal for this member.", vehicle_id)
            reading = None
        if reading and reading.get("recorded_at"):
            recorded_at = datetime.fromisoformat(reading["recorded_at"])
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            context["days_since_last_use"] = (datetime.now(timezone.utc) - recorded_at).days

    try:
        context["pause_count_last_year"] = subscription_tools.get_pause_count(tenant_id, member["id"])
    except httpx.HTTPError:
        logger.warning("subscription-svc unreachable fetching pause count for member %s — no pause signal for this member.", member["id"])

    return context


async def run_churn_sweep(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    """Evaluates every member once. Returns a real summary rather than
    just logging — a CronJob's exit status and logs are the only
    visibility ops has into whether this did anything useful."""
    try:
        members = subscription_tools.list_members(tenant_id)
    except httpx.HTTPError:
        logger.exception("Could not list members from subscription-svc — sweep aborted, nothing evaluated.")
        return {"evaluated": 0, "crossed_intervention_threshold": 0, "skipped_no_context": 0, "aborted": True}

    evaluated = 0
    crossed_threshold = 0
    skipped_no_context = 0

    for member in members:
        context = _build_churn_context(tenant_id, member)
        if not context:
            # Not necessarily a problem — a brand-new member may
            # genuinely have no telemetry or pause history yet — but
            # worth counting so a sweep that skips most of the base
            # is visible rather than silently "successful."
            skipped_no_context += 1
            continue

        result = await member_experience_agent.evaluate_churn_risk(db, tenant_id, member["id"], context)
        evaluated += 1
        if "retention_bonus_awarded" in result.decision_log.action_taken:
            crossed_threshold += 1

    summary = {"evaluated": evaluated, "crossed_intervention_threshold": crossed_threshold, "skipped_no_context": skipped_no_context, "aborted": False}
    logger.info("Churn sweep complete: %s", summary)
    return summary
