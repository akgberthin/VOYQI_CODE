"""
Autonomy tier lookup — the single source of truth for whether an agent
action executes immediately, waits for approval, or requires a named
accountable human before it can be approved at all.

docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §1.1: "This file [the
policy YAML] is the single source of truth for autonomy decisions — do
not hardcode tier logic in application code." Every call site passes
its (agent, action_type, context) through get_tier() rather than
branching on a locally-defined threshold.
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.core.config import get_settings


@lru_cache(maxsize=1)
def _load_policy() -> dict:
    settings = get_settings()
    path = Path(settings.tier_policy_path)
    return yaml.safe_load(path.read_text())


def _evaluate_threshold(threshold: str, context: dict[str, Any]) -> bool:
    """Threshold expressions in the policy are simple comparisons like
    "cost_delta_usd > 150" against a single context field. Anything
    that doesn't parse this way is treated as not met — a malformed or
    unrecognized threshold must never silently pass as "met" and drop
    an action into a lower tier than configured."""
    for op, fn in ((">=", lambda a, b: a >= b), ("<=", lambda a, b: a <= b),
                   (">", lambda a, b: a > b), ("<", lambda a, b: a < b),
                   ("==", lambda a, b: a == b)):
        if op in threshold:
            field, _, raw_value = threshold.partition(op)
            field = field.strip()
            raw_value = raw_value.strip()
            if field not in context:
                return False
            try:
                value = float(raw_value)
                observed = float(context[field])
            except (TypeError, ValueError):
                return False
            return fn(observed, value)
    return False


def get_tier(agent: str, action_type: str, context: dict[str, Any] | None = None) -> int:
    """
    Returns 1, 2, or 3 for the given agent/action_type pair. Falls back
    to policy['default_tier'] (2) if the pair isn't listed at all —
    silence must never mean autonomous. When a threshold expression is
    present and not met, the action escalates to the next tier up
    rather than guessing (capped at 3) — an ambiguous evaluation must
    never be treated as clearance for a *lower* tier than configured.
    """
    policy = _load_policy()
    action_cfg = policy.get("agents", {}).get(agent, {}).get("actions", {}).get(action_type)
    if action_cfg is None:
        return policy["default_tier"]

    tier = action_cfg["tier"]
    threshold = action_cfg.get("threshold")
    if threshold and not _evaluate_threshold(threshold, context or {}):
        return min(tier + 1, 3)
    return tier


def approval_required(tier: int) -> bool:
    return tier >= 2


def requires_accountable_role(tier: int) -> bool:
    return tier >= 3
