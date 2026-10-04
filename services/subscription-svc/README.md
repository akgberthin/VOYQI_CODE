# subscription-svc

Execution layer for **VOYQI Mobility — Membership & Subscription
Administration** (Section 5), MOB-001 through MOB-010. Port **8003**.
Owns enrollment, billing, the GU economy, and lifecycle for the Portable
Mobility(TM) subscription. Per the spec, member records here are meant
to eventually be "a view into the Enterprise CRM," which isn't built
yet — `MemberProfile` is self-contained for now.

## What's real here — the fourth specialist agent

- **MOB-007 Pause/Resume/Cancellation** — the **Member Experience
  Agent** (ai-orchestration-svc) evaluates every cancellation for a
  win-back offer via a real HTTP call, but **a member's right to cancel
  is never blocked on it**: `initiate_cancellation` proceeds with
  `winback_offer: null` if the agent is slow, errors, or recommends
  nothing — cancellation itself only ever happens in the separate
  `finalize_cancellation` step, which never depends on the agent call.
  The offer logic itself distinguishes retainable reasons (price,
  service issues) from non-retainable ones (relocating, deceased) —
  not every cancellation should get a discount pitch.
- **MOB-001 churn-risk scoring** — same agent, a deterministic
  RFM-style heuristic (days idle, pause count, support complaints, GU
  engagement) that's fully auditable in its own reasoning string, with a
  live Claude path that reads real member context first.
- **MOB-005 GU Economy fraud detection** — a rolling-window earn-count
  threshold; entries past it still complete (the member still gets
  their GU) but get `flagged_for_fraud` and publish `gu.fraud_suspected`,
  which ai-orchestration-svc's Compliance/Audit Agent always reviews —
  the same "complete the action, flag it separately" pattern used for
  guardrail-violating marketing content elsewhere on the platform.
- **MOB-006 Vehicle Swap** and **MOB-008 Global Mobility Continuity** —
  both call real fleet-svc endpoints to check class-eligible
  availability and propose a transfer, using the exact same
  PROPOSED-not-dispatched contract the Fleet Intelligence Agent's own
  tool uses. A swap or travel-continuity request never skips the
  human-dispatch step just because it originated from a member action
  instead of an AI recommendation.

## Deterministic, honestly thinner AI tie-in

- **MOB-002** Subscription Plan Manager — class/market pricing,
  class-equivalency rules (falls back to the source class unchanged if
  no rule is configured for a market, so continuity never fails
  outright on a missing rule)
- **MOB-003** Enrollment & Onboarding — application review gate (must be
  APPROVED before `enroll()`), VOYQI Card issuance/binding
- **MOB-004** Billing & Invoicing — proration, dunning (capped retries,
  then PAST_DUE for a human collections workflow), refunds
- **MOB-009** VOYQI Black(TM) Administration — invitation flow, kept in
  its own tables since Black is a separate brand, never a tier
- **MOB-010** Concierge Operations — journey tracking with a mode field
  (proactive/reactive/assisted) for the white-glove journey board; no
  dedicated Concierge Agent was built this pass — the documented nucleus
  lists Concierge as its own specialist, and building it out honestly is
  future work rather than folding it artificially into this one

## Tests

```bash
python -m pytest tests/ -v   # 68 passing, 0 failures
ruff check .                 # 0 lint errors
```
