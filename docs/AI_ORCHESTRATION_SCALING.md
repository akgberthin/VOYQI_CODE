# AI Orchestration — Realtime / Batch Split (REV D)

This document records why the AI orchestration nucleus was split into two
deployment groups, and confirms what did **not** change.

## What did not change

This is still **one logical AI service** — one LangGraph graph, one image,
one Supervisor coordinating specialist agents. PRD §23.1 is explicit:
*"Build the AI as multiple separate services" is a "Do Not" — multiple
independent AI services do not share context. One orchestration service
with multiple agents is the rule.* Nothing below violates that. The split
is a Kubernetes scheduling boundary, not a service boundary:

- Both deployment groups run from the same container image.
- Both read and write the **same** Postgres database (`ai_orchestration_service`
  on the core Cloud SQL instance) for the decision log.
- Both read and write the **same** Redis-backed LangGraph checkpoint store
  (`LANGGRAPH_CHECKPOINTER=redis`, namespace `voyqi-ai-nucleus`).
- Agent-to-agent coordination (e.g., Supervisor routing a request that
  touches both a realtime and a batch agent) still happens through that
  shared state — never a direct pod-to-pod call between the two groups
  (deliberately no NetworkPolicy rule permits `tier=realtime` ↔
  `tier=batch` traffic).

## What changed, and why

Three concrete compromises were identified and fixed:

### 1. Node-level resource contention

**Before:** Concierge (latency-critical — a member is watching a chat
window) and Fleet Intelligence / Pricing Intelligence (throughput-heavy
batch reasoning) shared 2 pod replicas on one tainted node pool. A batch
job chewing through a fleet-wide reassignment calculation could starve the
CPU/memory Concierge needed for a real-time reply.

**Fix:**
- Two node pools instead of one — `ai-orchestration-realtime` and
  `ai-orchestration-batch` (`terraform/modules/gke`). Different physical
  nodes; a batch pod maxing out its node cannot touch realtime capacity.
- Realtime pods run **Guaranteed QoS** (`requests == limits`) — the
  kubelet never throttles or evicts them ahead of a lower-priority pod.
  Batch stays **Burstable** — it can tolerate being squeezed; it must
  never be the one squeezing.
- Realtime pods carry `PriorityClass: voyqi-ai-realtime-critical` (900000);
  batch carries `voyqi-ai-batch-standard` (100000). Under any eviction
  pressure, batch goes first, always.
- Kafka consumer groups are split (`ai-orchestration-realtime` vs.
  `ai-orchestration-batch`), each subscribed only to its own topic set — a
  batch-side backlog can no longer delay realtime event processing,
  because it isn't the same consumer process pulling from every topic.
- HPA signals differ by tier: realtime scales on a p95-latency custom
  metric (falls back to CPU until the Prometheus Adapter metric is wired
  up); batch scales on CPU utilization, which is fine to lag slightly.

### 2. Shared Anthropic API rate-limit budget

**Before:** All agents drew from one API key with no per-tier quota. A
burst of chat traffic could exhaust the budget Pricing Intelligence needed
for a scheduled run, and vice versa.

**Fix:**
- Two Anthropic Console **Workspaces** — `voyqi-ai-realtime` and
  `voyqi-ai-batch` — each with its own API key and its own rate-limit
  allocation (RPM / input-TPM / output-TPM), carved out of the org total.
  A burst on one side structurally cannot touch the other's allocation.
  (`ANTHROPIC_API_KEY_REALTIME`, `ANTHROPIC_API_KEY_BATCH` — see
  `.env.example`.)
- Genuinely non-time-critical batch reasoning (a scheduled fleet-wide
  reassignment calculation, a pricing model recalculation run) routes
  through Anthropic's asynchronous Batch API instead of the standard
  Messages API (`ANTHROPIC_BATCH_API_ENABLED=true`). Batch-tier requests
  run on separate infrastructure at a materially lower price than
  synchronous calls, so they don't compete for *either* Workspace's
  rate-limit pool. Reactive/event-driven calls that need a sub-second
  decision (e.g., surge pricing on `rental.booked`) stay on the standard
  synchronous path in the batch Workspace — only the delay-tolerant work
  moves.
- Confirm current RPM/TPM figures and Batch API turnaround times against
  `docs.claude.com` before finalizing capacity planning — Anthropic
  adjusts tiers periodically.

### 3. Blast radius on deploy/crash

**Before:** All agents lived in one deployable unit. Redeploying to
change Pricing Intelligence's logic took Concierge down too — shared
process, shared version.

**Fix:**
- Most agent iteration is behavioral, not structural — prompt wording,
  thresholds, tool permissions. Those changes should ship through the
  **Prompt & Policy Manager** (AI-003 in the Enterprise Admin Screen
  Specification): version-controlled, approved, rollback-able, pulled at
  runtime from Postgres/Redis. Zero pod redeploy for the majority of
  changes, for either tier.
- For actual code deploys (a new tool, a new routing edge): `Deployment`
  strategy is `RollingUpdate` with `maxUnavailable: 0` on both tiers, plus
  a `PodDisruptionBudget` (`minAvailable: 2` of 3 on realtime,
  `minAvailable: 1` of 2 on batch) so a rollout or node drain never drops
  below meaningful capacity.
- Because realtime and batch are separate Deployments, a batch-tier
  rollout is now structurally incapable of affecting Concierge — not just
  "unlikely to," but architecturally unable to, regardless of rollout
  strategy.
- Canary before promote: roll the new image to one pod, run the
  evaluation suite the Prompt & Policy Manager already requires before
  any promotion, then roll the rest of that tier.

### 4. State / session affinity

**Before:** If LangGraph's checkpoint state lived in process memory, a
conversation's graph state was pinned to whichever replica started it —
load balancing across replicas didn't actually distribute load evenly for
long-running sessions.

**Fix:** LangGraph's checkpointer is externalized to Redis
(`LANGGRAPH_CHECKPOINTER=redis`), keyed by `thread_id` (the conversation/
session ID) — matching the platform's existing Redis-short-term /
Postgres-long-term memory design. Any replica in the realtime tier can
now service any turn of any conversation; no sticky sessions required. A
pod restart or scale-down never loses a conversation mid-stream, because
the source of truth was never in that pod's memory to begin with.

## Roster expansion — 7 agents → 12

The original roster (Supervisor + Fleet Intelligence, Member Experience,
Pricing Intelligence, Concierge, Revenue, Retention) was scoped entirely to
the core Mobility PRD v3 — before the Enterprise Admin Screen Specification
added the 12 Layer 2 services. That spec's AI Orchestration Administration
section (AI-001) already assumed "CRM, Originations, and Talent CRM
agents" that were never actually defined in the PRD or the Software
Development Agreement's committed roster — a real gap between documents,
introduced across separate build sessions rather than resolved in either.

Five agents close that gap, matched to distinct judgment domains — not
1:1 with all 26 services, which would proliferate deterministic-fallback
maintenance and Anthropic rate-limit contention disproportionately to
actual call volume:

| New agent | Tier | Closes the gap for | Why this tier |
|---|---|---|---|
| **Relationship Intelligence** | Realtime | `crm-svc` — Unified Customer 360, AI Lead & Opportunity Engine, Conversational Engagement Hub (CRM-001–003) | Staff wait on a drafted reply in-session in the omnichannel inbox — same latency profile as Concierge, not a batch job. |
| **Sales & Originations Intelligence** | Batch | `sales-service`, `dms-svc`, `originations-svc` — pipeline, F&I decisioning, lender matching, e-contracting | Event-reactive decisioning (a submitted deal, a credit request), not a live chat — same profile as Pricing Intelligence. |
| **Talent & Workforce Intelligence** | Batch | `hrm-svc` — Academy course-prep, Notices & Communication drafting, candidate screening | Formalizes what was previously a bolted-on AI call inside the HRM module itself, run under the Supervisor like every other agent instead of outside its governance (AI-001/002/003/004 now apply to it). Not latency-sensitive. |
| **Compliance & Risk Intelligence** | Batch | `security-compliance-svc`, `insurance-risk-svc` — ABAC policy simulation, audit anomaly detection | Analytical, run against historical data; never a same-second requirement. |
| **Portfolio Intelligence** | Batch | `executive-svc`, `management-svc`, `real-estate-svc`, `capital-finance-svc` — cross-subsidiary rollups | Lower call volume (P1/P2 screens) — one shared agent across four related, lightly-loaded domains rather than four separate ones. |

`technology-ip-svc` has no dedicated agent yet — P2, not enough defined
AI-relevant decisions in the Screen Spec to justify one. Add it to
Portfolio Intelligence's scope if that changes, rather than creating a
sixth batch agent for it.

Kafka topics for the five new agents follow the platform's existing
event-naming convention (`noun.verb_past` / `noun.event`) and are wired
into `KAFKA_TOPICS` on the appropriate deployment
(`infrastructure/kubernetes/base/ai-orchestration/deployment.yaml`):
`deal.submitted`, `credit.decision.requested`, `lender.matched` (Sales &
Originations); `course.draft.requested`, `notice.draft.requested`,
`candidate.screened` (Talent & Workforce); `policy.simulation.requested`,
`audit.anomaly.detected` (Compliance & Risk); `portfolio.rollup.scheduled`
(Portfolio); `lead.captured`, `conversation.message.received`,
`conversation.reply.drafted` (Relationship Intelligence).

Each new agent still needs its own deterministic fallback before it can
move from `PLANNED`/`ACTIVE_DEV` to `READY`, per the platform's two-layer
agent discipline — the same bar every existing agent already meets.

## Autonomy tiering — replacing the blanket approval rule

"AI proposes, human approves" as a blanket rule for every AI action means
a human clicks approve on routine, reversible, low-stakes work that never
needed a person — the platform becomes a recommendation engine, not an
automated one. This is being replaced with a three-tier model:

- **Tier 1 — fully autonomous.** Reversible, low-stakes, high-volume.
  Executes immediately, logs to the decision log / DFEED, reviewable after
  the fact. No approval card.
- **Tier 2 — approval gate required.** Low confidence, above a cost/scale
  threshold, or otherwise flagged.
- **Tier 3 — always human, no exceptions.** Regulatorily required
  accountable human decision (fair lending, employment law, access-policy
  grants). Not reachable via any threshold change — a hard block, not a
  convention.

The mechanism lives in
`infrastructure/kubernetes/base/ai-orchestration/tier-policy.yaml`
(mounted into both deployment tiers) and applies to every agent — the
realtime/batch scheduling split above and the tier model are independent
axes; an agent's tier assignment doesn't change which node pool it runs
on. Full per-module rollout instructions, including the specific diff for
Fleet Management's already-integrated code, are in
**`docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md`** — that document is
written for the engineers actually updating the server build and is the
one to hand off directly.

## Why not go fully independent — one agent, one microservice

Considered and rejected as the default, for reasons specific to what this
fixes and what it would cost:

- Every agent hand-off (Supervisor→Concierge, Concierge→Member Experience
  mid-conversation) is currently an in-process function call passing graph
  state by reference. Full separation makes every one of those an HTTP
  call plus serialization — added latency exactly on the path this split
  was meant to protect.
- The Supervisor's "global platform context" is only global because it's
  one process with shared memory. Fully split, that context has to be
  explicitly passed across service boundaries on every hop — rebuilding,
  over a network, what LangGraph already provides in-process.
- n independent services means n complete infra footprints (health checks,
  CI/CD, RBAC entries, cost dashboards, on-call) regardless of actual call
  volume — disproportionate for agents that may see a handful of
  invocations a day.
- Prompt-caching and shared-context reuse across a reasoning chain is
  easiest within one client/session; full separation tends to mean
  resending shared context on every hop, raising both cost and latency.

Full independence remains the right call for any *future* agent whose
isolation need is structural or regulatory rather than throughput-related
— e.g., a hypothetical Credit Decisioning agent for lending, which would
need its own audit boundary and fair-lending explainability trail separate
from consumer-facing agents. That is a case-by-case decision per agent,
not a reason to decompose the current six.
