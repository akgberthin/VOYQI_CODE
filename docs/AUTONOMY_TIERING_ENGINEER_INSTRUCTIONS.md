# Autonomy Tiering — Engineer Build Instructions

**Audience:** the engineering team updating the live server build.
**Status:** this is the target-state spec, written as if no tiering work
has happened yet. It intentionally includes Fleet Management (FLT) even
though FLT's AI integration is already complete and live — treat the
Fleet section below as the diff to apply to that existing, working code,
not as new module scope. Nothing in this document should be applied to
the server until you're ready to schedule it; it's the instruction set for
that future update, not a description of the server's current state.

**Why:** "AI proposes, human approves" as a blanket rule for every AI
action makes the platform a recommendation engine, not an automated one —
a human clicks approve on routine, reversible, low-stakes actions that
never needed a person. This document replaces the blanket rule with three
tiers and gives you the exact config, code changes, and per-module
checklist to implement it. Full rationale and the tier definitions:
`docs/AI_ORCHESTRATION_SCALING.md` and the header of
`infrastructure/kubernetes/base/ai-orchestration/tier-policy.yaml`.

---

## 1. The mechanism

`infrastructure/kubernetes/base/ai-orchestration/tier-policy.yaml` is a
ConfigMap, already mounted at `/etc/voyqi/tier-policy.yaml` in both
`ai-orchestration-realtime` and `ai-orchestration-batch` (see
`TIER_POLICY_PATH` env var on both deployments). It maps every
`agent.action_type` to a tier (1/2/3) and, where relevant, a threshold
expression. **This file is the single source of truth for autonomy
decisions — do not hardcode tier logic in application code.**

### 1.1 Required code change in the AI orchestration service

Add a lookup used before every agent action executes:

```python
# services/ai-orchestration-service/src/core/tiering.py
import yaml
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=1)
def _load_policy() -> dict:
    path = Path(os.environ["TIER_POLICY_PATH"])
    return yaml.safe_load(path.read_text())

def get_tier(agent: str, action_type: str, context: dict) -> int:
    """
    Returns 1, 2, or 3. Evaluates the action's threshold expression (if
    any) against `context`. Falls back to policy['default_tier'] (2) if
    the agent/action_type pair isn't listed — silence must never mean
    autonomous.
    """
    policy = _load_policy()
    action_cfg = (
        policy.get("agents", {})
        .get(agent, {})
        .get("actions", {})
        .get(action_type)
    )
    if action_cfg is None:
        return policy["default_tier"]

    tier = action_cfg["tier"]
    threshold = action_cfg.get("threshold")
    if threshold and not _evaluate_threshold(threshold, context):
        # threshold not met -> action doesn't qualify for the tier as
        # configured; escalate to the next tier up rather than guessing
        return min(tier + 1, 3)
    return tier
```

### 1.2 Required execution branching

Every agent action, in every agent, routes through this same three-way
branch — no agent gets to skip it:

```python
tier = get_tier(agent=self.name, action_type=action.type, context=action.context)

if tier == 1:
    result = execute(action)                      # runs immediately
    decision_log.record(action, result, tier=1, approval_required=False)
    dfeed.unshift(action, result)                  # existing FLT-pattern event feed
elif tier == 2:
    pending = recommendation_queue.create(action, tier=2)
    surface_in_ai002(pending)                      # Recommendation Review & Override
    # execution blocks on human approve/modify/reject — existing pattern, unchanged
else:  # tier == 3
    pending = recommendation_queue.create(action, tier=3, requires_accountable_role=True)
    surface_in_ai002(pending)
    # identical queueing to tier 2, but the UI must require selecting a
    # named accountable human role before the approve button is enabled,
    # and that role is permanently recorded against the decision —
    # this is the fair-lending / employment-law requirement, not a
    # cosmetic difference from tier 2
```

**Tier 3 is not "tier 2 with extra paperwork."** The UI and the audit
record must capture *who* is accountable, not just that a human clicked
something. Do not implement tier 3 as a copy of tier 2's approval card.

### 1.3 AI-002 (Recommendation Review & Override) changes

AI-002 currently queues everything. After this change, it queues **only**
tier 2 and tier 3 items. Add a filter/tab so operators can still see tier
1 actions after the fact (read from the decision log, not the queue) —
they need visibility, just not a gate.

### 1.4 AI-005 (Goal & Threshold Management) changes

AI-005 already lets operators set optimization targets per agent. Extend
it to also edit the `threshold` expressions in `tier-policy.yaml` — but
**enforce in code, not just convention, that tier 3 action types cannot
have their tier changed from AI-005.** This must be a hard block in the
service, not a documented rule operators are trusted to follow.

### 1.5 AI-004 (Audit & Explainability Log) — graduation data

Every tier 1 and tier 2 decision already gets logged with `tier` and
`approval_required` fields (§1.1–1.2). Add a report view: per
`agent.action_type`, approval rate and override rate over the trailing 90
days. This is the evidence used to promote a tier 2 action type to tier 1
— see §5.

---

## 2. Fleet Management (FLT) — diff to apply to already-integrated code

**Directive: Fleet is promoted to full automation.** Every AI action type
below moves to tier 1 except the two that are not a tiering question at
all — liability assessment and tamper-event flagging are human-only
fields structurally excluded from the AI decision path in the data model
(PRD's "structural separation of AI-judged vs. human-only fields" rule).
Full automation means everything AI is *permitted* to decide, it now
decides without a gate — it does not mean expanding what AI is permitted
to decide. Those two stay out of the AI path regardless of tiering.

Apply `tiering.py` (§1.1–1.2) to the existing `fleet-intelligence` agent
calls and reclassify per the table below. **Existing deterministic
fallback logic, `.aip`/`.aip-h`/`.aip-body` styling, and `DFEED.unshift()`
calls are unchanged — only whether the approval card renders before
execution changes.**

| Module | Existing AI behavior | New tier | What changes on the server |
|---|---|---|---|
| FLT-003 Staging & Hub Allocation | Same-region rebalance ≤3 vehicles | **1** | Executes immediately, card removed, still logged to feed |
| FLT-003 Staging & Hub Allocation | Cross-region or >3-vehicle reassignment | **1** *(promoted from 2)* | Card removed — executes immediately, logged. See promotion note below. |
| FLT-004 Maintenance Scheduler | Predictive alert (informational) | **1** | Already informational — confirm explicitly tagged tier 1, not left to default |
| FLT-004 Maintenance Scheduler | Auto-schedule work order, cost < $500 | **1** | Executes immediately, logged |
| FLT-004 Maintenance Scheduler | Auto-schedule work order, cost ≥ $500 | **1** *(promoted from 2)* | Card removed — executes immediately, logged. See promotion note below. |
| FLT-005 Telematics & IoT | Tamper event flag | **3 — unchanged** | No change. Not eligible for promotion at any point — human-only field, not an approval-gate question. |
| FLT-008 Incident, Damage & Claims | Severity triage | **1** | Executes immediately, logged |
| FLT-008 Incident, Damage & Claims | Liability assessment | **3 — unchanged** | No change. Not eligible for promotion at any point — same reasoning as FLT-005. |
| FLT-006/007 Disposition | Channel recommendation | **1** | Executes immediately — routes to the recommended channel, logged |
| FLT-006/007 Disposition | Actual disposition execution (sale/transfer) | **1** *(promoted from 2)* | Card removed — executes immediately, logged. See promotion note below. |
| FLT-008/009 Financing & Floorplan | Covenant early-warning (80%-of-limit flag) | **1** | Informational — confirm explicitly tier 1, not defaulted |

### Full automation promotion — what to verify before this ships

The four rows marked "promoted from 2" are a **directed promotion**, not a
90-day-evidence graduation (§5's normal path). That's a legitimate way to
move an action type — Fleet has months of real production history these
action types were already gating — but it means the evidence-gathering
happens now, not before the fact, as it would with a fresh module. Before
this goes to production, pull the following from the existing audit log
for each of the four promoted action types and keep it on record:

- Historical approval rate (expect high) and override rate (expect low)
  for cross-region reassignment, high-cost maintenance scheduling, and
  disposition execution.
- Confirm no downstream consumer (billing, insurance, reporting) hard-
  coded an assumption that a human-review event always precedes execution
  for these action types — grep for subscribers to
  `human.review.pending` / `fleet.reassign.proposed` specifically, since
  those events won't fire for tier-1 actions anymore.
- Roll out behind a feature flag per action type rather than all four at
  once — same staged approach as any production change, not a special
  requirement of tiering.

If the historical data for any one of the four doesn't actually support
full automation (e.g., disposition execution turns out to have a
meaningful override rate), leave that specific action type at tier 2 and
flag it back — this table is the target, not an instruction to ship
regardless of what the data says.

**Verification requirement, matching the existing FLT test discipline:**
for every recommendation type now at tier 1, add a test asserting the
approval card does NOT render and the action executes and logs to
`DFEED` — mirroring the same rigor as the original 61-assertion jsdom
smoke test. For the two tier 3 items, add a test asserting the approval
card DOES render and cannot proceed without an accountable-role
selection, and that neither is reachable from any threshold or promotion
path in AI-005.

---

## 3. HRM (Academy, Notices) — build in from the start

Neither HRM-006 (Academy) nor HRM-008 (Notices & Communication) is wired
to the real Anthropic Messages API yet. When that wiring happens:

- Course-prep draft generation → **tier 1**. A draft is not a decision;
  nothing executes until a human chooses to use it in a course, so there's
  no action here that needs gating in the first place — but log it.
- Notice/communication draft generation → **tier 1**, same reasoning.
- Any future feature that executes a personnel decision (hire, reject,
  discipline) → **tier 3**, always. This does not exist in the current
  build; if it's ever added, it goes straight to tier 3 in
  `tier-policy.yaml` before any code ships, not after.

## 4. Super-admin-svc, and the 10 PLANNED Layer 2 services

None of these have application code yet — this is the cheap case. Build
the tier lookup (§1.1–1.2) in from day one using the tiers already
assigned in `tier-policy.yaml`:

- **crm-svc** (Relationship Intelligence) — routine replies and lead
  routing are tier 1; anything with negotiation/pricing/commitment
  language is tier 2.
- **dms-svc / originations-svc** (Sales & Originations Intelligence) —
  lead scoring and lender matching are tier 1; deal structuring is tier 2;
  **credit decisions are tier 3, hard-coded, no exceptions** — this is a
  fair-lending requirement, confirm with legal/compliance before this
  service goes to `READY`, not after.
- **security-compliance-svc / insurance-risk-svc** (Compliance & Risk
  Intelligence) — anomaly flagging and policy simulation are tier 1;
  **actually applying an ABAC policy grant is tier 3, hard-coded.**
- **executive-svc / management-svc / real-estate-svc /
  capital-finance-svc** (Portfolio Intelligence) — reporting/rollups only,
  tier 1 across the board. There is nothing in this agent's current scope
  that executes an action requiring approval; if that changes, update
  `tier-policy.yaml` before shipping the feature that needs it.

Do not mark any of these `READY` in `services-catalog.yaml` until the
tier lookup is wired and the tier-3 hard-block (§1.4) is in place and
tested.

---

## 5. Rollout plan

1. **Ship conservative — for new modules.** For any action type in a
   module without production history (HRM, super-admin-svc, the 10
   PLANNED services), leave it at tier 2 for the first deployment. It's
   cheap to promote later with evidence; it's expensive to walk back an
   autonomous action that shouldn't have been.
2. **Collect 90 days of AI-004 data** per action type: approval rate,
   override rate.
3. **Promote tier 2 → tier 1** only for action types with a high approval
   rate and low override rate, through the same evaluation-suite-before-
   promotion gate AI-003 already uses for prompt changes — this is a
   config change to `tier-policy.yaml` reviewed like any other production
   change, not a silent drift.
3a. **Directed promotion — for modules with existing production
   history.** Fleet Management (§2) used this path: rather than waiting
   90 days going forward, pull the *existing* audit log history for the
   action types being promoted and treat that as the evidence, before
   the code ships rather than after. This is legitimate — it's the same
   evidence requirement as step 3, just already available — but it means
   the verification in §2 is a precondition, not a follow-up task.
4. **Never promote tier 3.** It's not on this ladder. If you think a tier
   3 action type should move, that's a legal/compliance conversation, not
   an engineering one.

---

## 6. Sign-off checklist before flipping a module to `READY`

- [ ] Every agent action the module triggers has an explicit entry in
      `tier-policy.yaml` — nothing relies on `default_tier` silently.
- [ ] Tier 1 actions: test asserts no approval card renders, action
      executes, decision is logged with `tier: 1`.
- [ ] Tier 2 actions: test asserts approval card renders, action does not
      execute until approved.
- [ ] Tier 3 actions: test asserts approval card renders AND cannot be
      approved without an accountable-role selection, and that the tier-3
      hard-block in AI-005 prevents changing this action's tier.
- [ ] AI-004 audit log correctly records `tier` and `approval_required`
      for every decision from this module.
- [ ] For any directly-promoted action type (§3a — e.g. Fleet's 4 rows
      promoted from tier 2 in this pass): historical approval/override
      rate pulled and recorded before shipping, not after.
- [ ] Legal/compliance sign-off obtained for any newly-added tier 3 action
      type (credit decisions, employment decisions, access-policy grants).
