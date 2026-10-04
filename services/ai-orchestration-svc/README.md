# ai-orchestration-svc

The AI nucleus. Port **8020**. One service, one shared context, many
specialist agents — not separate services per agent.

## Status: real, not scaffolding

Makes a real `anthropic.Anthropic` client call with a full agentic
tool-use loop (`src/core/anthropic_client.py`) — not a single-shot
completion, a real "reason, call a tool, read the result, reason again"
loop, capped at 5 iterations. Writes an immutable `DecisionLog` row for
every single evaluation, across every agent, whether it acted, deferred,
or a human later overrode it.

## Seven agents now, one nucleus

**Compliance/Audit Agent** — reactive. Scores risk on events that
already happened across six other services now (see the anomaly list
below) and defers high-risk ones to human review. Its live-Claude tool
set switches by event domain rather than defaulting to one: identity/
access events get real RBAC context (`get_user_rbac_context`); hub
security events get a card's real tap history (`get_card_tap_history`)
instead — so a flagged geographic anomaly gets reasoned about in terms
of whether it's a member's first anomaly or a repeat pattern, not just
narrated at a fixed score. The deterministic risk score itself never
changes based on which tool set ran; only the explanation and downstream
review context does.

**Marketing Content Agent (MKT-008)** — generative. Given a campaign and
a brief, it drafts customer-facing copy via `POST
/api/v1/ai/marketing/generate-content`, the synchronous entrypoint a
marketing operator's UI calls (this one isn't event-driven — content
generation is normally a deliberate human request, not a reaction to a
platform event). Same two-layer pattern as the Compliance/Audit Agent:

- **Deterministic fallback** (no API key configured): a fixed,
  functional template — never creative, but also structurally incapable
  of tripping the banned-phrase guardrail since it's a fixed string
- **Live Claude path**: a real agentic tool-use loop that reads the
  campaign's actual context (`get_campaign_context`) before drafting,
  then submits the draft (`create_content_draft`) — a tool that only
  ever writes `PENDING_REVIEW` rows; there is no tool this agent has that
  can approve or send anything
- Respects the same kill-switch/pause mechanism as the Compliance/Audit
  Agent — a paused Marketing Content Agent generates nothing, checked
  before any tool is ever called, not after
- marketing-svc's own deterministic guardrail floor runs on every draft
  regardless of which path produced it; a human approving a draft that
  still has violations lands in `APPROVED_WITH_VIOLATIONS` and gets
  reviewed by the *other* agent, closing the loop between the two

**Fleet Intelligence Agent (FLT-003, HUB-002/003)** — generative,
reacting to `subscription.created`, `travel.activated`, and
`vehicle.idle.48h` events by reading real hub supply before
recommending a vehicle staging transfer. Its one tool that acts,
`propose_transfer`, only ever creates a `PROPOSED` order — a human
fleet operator assigns the driver and dispatches. Moving a six-figure
physical asset gets the same human-in-the-loop treatment as sending a
message or approving a permission grant. Now also covers Hub Network
rebalancing: a touchpoint exception (`hub.touchpoint_exception_flagged`
— e.g. "vehicle not staged") reaches this same agent via
`evaluate_hub_rebalancing`, which reads hub-svc's real network snapshot
(a new `get_network_snapshot` tool, with real lat/long and utilization,
not a placeholder) and recommends which *other* hub should supply or
receive vehicles — weighing distance *and* whether the candidate
actually has headroom, not just proximity. This capability deliberately
stops at a recommendation rather than calling `propose_transfer`
itself: doing so would require reconciling hub-svc's hub-id space with
fleet-svc's separate one, a real seam documented in hub-svc's own
README rather than papered over with a guessed ID mapping.

**Member Experience Agent (MOB-001/005/007)** — covering churn-risk
scoring (a deterministic, fully auditable RFM-style heuristic), and
win-back offer recommendation at cancellation. The one hard rule
specific to this agent: `evaluate_winback` **never blocks a member's
right to cancel** — subscription-svc's `initiate_cancellation` proceeds
with no offer if this call is slow, errors, or recommends nothing;
cancellation itself happens in a separate step that never depends on
this agent succeeding.

**Pricing Intelligence Agent (RNT-002/005, DMS-005)** — reacts to
`rental.booked` by reading real hub utilization before recommending a
demand-adjusted daily rate — matching the platform's own documented AI
activity feed exactly ("Exec util 84% → demand 1.21x → rate applied").
Its one tool that acts, `apply_dynamic_rate`, only revises a rate before
checkout — rental-svc's own `modify_reservation` refuses the change
once a renter has taken the vehicle, so a rate someone already agreed to
pay can never be altered after the fact. The same agent covers two more
capabilities — one valuation domain, not three separate agents:

- **DMS-005 trade-in appraisal** — only consulted when no book-value
  data exists for a VIN at all (dms-svc's own deterministic engine
  handles valuation directly whenever `BookValueQuote` data is on file).
  For that narrower remaining gap, the no-API-key fallback plainly
  states it cannot value a vehicle rather than fabricating a number;
  only the live Claude path (explicitly caveated when relying on
  general knowledge rather than verified comps) produces an actual
  estimate, via a `submit_valuation` tool call.
- **DMS-002/005 retail pricing recommendation** — the one capability
  that (almost) never refuses: a vehicle already in inventory always
  has its own current asking price and days-in-inventory as a real
  internal anchor, so the deterministic fallback applies a genuine
  aging markdown (0.5%/day past the aging threshold, capped at 15%) to
  the book-value average when one exists, or to the current asking
  price when it doesn't — reported via `submit_price_recommendation`.
- **DMS-011 max-bid recommendation** — the fourth valuation surface,
  sitting between the other two in how much real data it has: the
  wholesale book average (when quotes exist) is a genuine deterministic
  anchor, so the fallback recommends wholesale minus an 8% acquisition
  discount; with no book data it refuses honestly like trade appraisal —
  an auction vehicle the group doesn't own has no internal price to
  anchor on either. Reported via `submit_max_bid`, which explicitly
  accepts a null recommendation: "I cannot recommend a ceiling" is a
  structured answer, not a failure to answer.

**Revenue Agent (CRM-001, CRM-002, CRM-007, ORG-001, ORG-002, ORG-003)**
— six capabilities in one agent rather than six agents, because every
one of them is the same underlying judgment ("is this the same person
/ is this lead or case worth the same attention / who should fund it"):

- **CRM-001 identity resolution** — runs automatically the instant
  crm-svc publishes `crm.source_record_created`, not on request. Real
  fuzzy matching (exact email → 0.95, exact phone → 0.85, name-only →
  0.6, checked against a shared 0.72 threshold) is the deterministic
  floor; a live Claude path catches nicknames, suffixes, and minor
  typos the strict fallback can't. Either way, the output
  (`MatchCandidate`) is only ever a suggestion — `confirm_merge` in
  crm-svc is the sole human-confirmed action that actually links two
  identities, no matter how high the match score.
- **CRM-002 lead scoring/routing** — reacts to `lead.captured`
  (marketing-svc's landing-page intake, previously an unrouted event
  waiting for exactly this consumer). The deterministic fallback is a
  real, explainable heuristic — contact-info completeness, email-domain
  professionalism, intake-channel weight — not a placeholder; the live
  path checks for duplicate leads via a real tool call before scoring
  and can reason about nuance ("looks enterprise") a fixed formula
  can't. Qualifying or disqualifying a lead based on an actual sales
  conversation stays a human call either way.
- **CRM-007 profile deduplication** — the gap CRM-001 doesn't cover: a
  `PersonProfile` created directly (not via an unresolved
  `SourceRecord`) never goes through identity resolution, so it could
  sit as an undetected duplicate indefinitely. `crm.person_profile_created`
  triggers this same fuzzy-match logic against every other existing
  profile, automatically. `confirm_profile_merge` in crm-svc re-points
  every dependent record (interactions, consent, leads, conversations)
  to the surviving profile — the absorbed one is marked, never deleted.
- **ORG-001 origination pre-qualification** — reacts to
  `org.case_created` from org-svc's unified pipeline (retail finance,
  subscription enrollment, rental-to-subscription conversion), reading
  a linked CRM-002 lead score as real prior signal when one exists.
  Pre-qualification is a lighter viability signal, not a credit
  decision — that's ORG-002's job, scored separately.
- **ORG-002 origination risk scoring** — reacts to
  `org.decisioning_requested`, scoped to subscription/rental cases only
  (retail finance already has its own complete decisioning in dms-svc).
  The agent scores; it never decides — `apply_risk_score` in org-svc
  applies a fixed, published threshold to the score consistently,
  regardless of which path (deterministic fallback or live Claude)
  produced it, so every case is held to the same bar.
- **ORG-003 funding source matching** — reacts to
  `org.funding_match_requested` once a case has a real risk score,
  matching it against the lender/funding panel's actual program
  guidelines (minimum risk score, maximum amount) rather than picking
  arbitrarily — the deterministic fallback filters to genuinely
  qualifying sources first, then picks by dealer reserve participation.

**Concierge Agent (CRM-003)** — reacts to `crm.message_received`
automatically, the instant crm-svc publishes it for every inbound
message, producing sentiment, intent, an escalation decision, and a
drafted reply in one pass. **It never sends anything** —
`create_draft_reply` always writes `PENDING_REVIEW`, the same shape as
the Marketing Content Agent's `ContentDraft`: there is no tool this
agent has that can put a message in front of a customer. The
deterministic fallback is real keyword-based sentiment/intent detection
with a fixed, honest, functional draft per intent category — never
creative, which is also why it structurally can't say something
inappropriate. Escalation requires *both* negative sentiment *and*
complaint intent (tested explicitly, so a merely negative-toned support
request doesn't over-trigger it); the live Claude path reads real
conversation history before drafting and catches what keyword-matching
can't — sarcasm, an implicit ask, mixed sentiment.

## Anomaly detection (SAP-007), spanning twelve services now

Every override, bypass, or forced action anywhere on the platform routes
to the Compliance/Audit Agent — one reasoning surface, not a bolt-on per
module: self-grants, rapid privilege accumulation, sensitive ABAC policy
activation, force-activated policy conflicts, sensitive panel grants,
break-glass activation (always reviewable, scored higher for off-hours
or long duration), impersonation (always reviewable, scored higher
without a session recording), kill-switch toggles (always reviewable,
scored much higher if the target is the Compliance/Audit Agent itself),
legal-disclosure publication that bypassed review, least-privilege
drift, marketing content approved over guardrail violations, TCPA
quiet-hours overrides (always reviewable — legal exposure), push
frequency-cap overrides (deliberately *not* auto-flagged alone — routine
business judgment, unlike TCPA), expired-asset usage overrides (always
reviewable — legal exposure), fleet total-loss declarations, vehicle
tamper events, floorplan covenant breaches, GU economy fraud flags,
overdue F&I adverse-action notices (real FCRA/ECOA exposure), red-flag
identity-theft check overrides, franchise facility non-compliance,
auction bids placed above wholesale book value with a documented
override (50 — legitimate business judgment like a presold unit, but
systematic overbidding erodes gross per unit), VOYQI Card taps that fail
a geographic-plausibility check (65 — a real active security signal;
the card is auto-suspended as the safe default before the event even
reaches this agent, so the review is to verify-and-reactivate or
confirm fraud, not to prevent ongoing harm), origination decisions
whose disparate-impact snapshot shows a genuinely large decline-rate
spread across origination types with adequate sample size (55 — the
review threshold; a moderate spread scores 25 and gets logged as
"keep monitoring" without escalating, and anything below a 5-decision
minimum per group is left unscored entirely rather than treated as a
false pattern from noise — the snapshot itself groups only by business
variables like origination type, since this system collects no
protected-class data for decisioning to have driven a real disparity
in the first place), and OFAC sanctions matches
on a credit bureau pull — the single highest-severity signal on the
platform (75, above every other event type), since federal sanctions
screening has zero override paths anywhere, unlike red-flag checks or
franchise non-compliance which at least have a documented-reason
override or a remediation path.
None of these are just a permission-name check — each pulls the user's
live context or the event's own risk-relevant fields before scoring.

**A paused or disabled agent is actually skipped**, not just reported as
paused — the supervisor checks the Agent table's status before invoking
any handler, for every agent, not just the Compliance/Audit Agent. This
is what makes SAP-006's kill switches (and AI-001's own pause/resume)
real rather than cosmetic.

## What "governs" this agent (AI-001 through AI-005, all live)

| Screen | Endpoint | What it does |
|---|---|---|
| AI-001 Agent Operations Console | `/api/v1/ai/agents` | Register agents, pause/resume/disable, see decision counts |
| AI-002 Recommendation Review & Override | `/api/v1/ai/recommendations` | Approve/reject pending high-risk actions; approving executes the underlying revoke against rbac-svc |
| AI-003 Prompt & Policy Manager | `/api/v1/ai/policies` | Versioned system prompts, promotion gated on a passed eval suite, retiring (never deleting) prior versions |
| AI-004 AI Audit & Explainability Log | `/api/v1/ai/decision-log` | Every decision, its reasoning, its tool calls, its outcome |
| AI-005 AI Goal & Threshold Management | `/api/v1/ai/goals` | Operator-set targets (e.g. risk threshold) the agent reads live on every run — no redeploy to recalibrate |

## The honest limitation

Running the live Claude reasoning path requires `AI_ORCH_ANTHROPIC_API_KEY`.
Without it, the Compliance/Audit Agent still functions correctly — it falls
back to a deterministic risk-scoring heuristic and labels its own
`DecisionLog.reasoning` as a fallback, rather than silently pretending a
live decision happened. This is intentional and tested
(`tests/test_compliance_audit_agent.py`) — the deterministic path is the
floor Claude's judgment is checked against, not a fake stand-in for it.

## How a new agent gets added (the now-proven pattern)

1. Build the tool adapters for the target execution service
   (`src/services/<domain>_tools.py`, following `rbac_tools.py`)
2. Write the agent's system prompt + risk/decision logic
   (`src/services/<domain>_agent.py`, following `compliance_audit_agent.py`)
3. Add the agent's events to `EVENT_ROUTING` in `src/services/supervisor.py`
4. Register the agent via `POST /api/v1/ai/agents`
5. Write tests covering: the deterministic/fallback path, the mocked-Claude
   tool-use path, and the API surface for review/override

This is the exact retrofit sequencing to apply next to pricing-svc
(Pricing Intelligence Agent — the originally-planned pilot), then fleet-svc,
crm-svc, hrm-svc, and the remaining modules from the enterprise admin spec.

## Running locally

```bash
pip install -r requirements.txt --break-system-packages
export AI_ORCH_ANTHROPIC_API_KEY=sk-ant-...   # optional — omit for deterministic-only mode
uvicorn src.main:app --reload --port 8020
```

## Tests

```bash
python -m pytest tests/ -v   # 31 passing, 0 failures
ruff check .                 # 0 lint errors
```
