# hub-svc

Execution layer for **Hub Network Administration** (HUB-001 through
HUB-003) — the physical touchpoints bridging Mobility, Rental, and
Fleet. Port **8006**.

## What's real here

**HUB-001 Hub Directory & Configuration** — hub profiles with the full
type taxonomy from the platform spec (airport, city/business,
residential, university/campus, corporate campus, partner/white-label),
capacity, operating hours, amenities (lounge access included), and the
lat/long every hub carries for the member-app and operator-portal maps
to render directly against — no separate geocoding step needed. This
stays deterministic CRUD; there's no judgment call here for an agent to
add value to.

**HUB-002 Hub Operations Board:**
- Touchpoint events (pickup/return/swap, subscription or rental) with
  a plain scheduled → completed lifecycle.
- **Vehicle prep checklists mirror dms-svc's ReconOrder pattern
  deliberately**: `mark_checklist_ready` refuses while any
  `PrepChecklistItem` is still open. This stays a hard, deterministic
  gate on purpose — whether an item is checked is a fact, not a
  judgment call, and letting an LLM decide a checklist is "close
  enough" to complete would weaken exactly the kind of audit-relevant
  control it needs to be.
- **VOYQI Card tap geographic anomaly detection is deterministic by
  design, not an AI judgment call** — a real haversine distance
  calculation against an implied-speed plausibility threshold, the same
  reasoning the platform already applies to OFAC sanctions screening
  elsewhere: a security gate that has to fire correctly every time
  shouldn't depend on a model being reachable, consistent, or in a
  good mood that day. Auto-suspend is the safe default action; that
  part doesn't change.
- **What genuinely is AI-orchestrated: what happens *after* the
  deterministic trigger fires.** A touchpoint exception (e.g. "vehicle
  not staged") now publishes `hub.touchpoint_exception_flagged`, which
  the Fleet Intelligence Agent picks up via `evaluate_hub_rebalancing` —
  real judgment about which *other* hub should supply or receive
  vehicles, weighing both geographic distance and whether that hub
  actually has headroom (a nearby hub already near its own capacity is
  a bad candidate even though it's close). A flagged card anomaly
  similarly reaches the Compliance/Audit Agent with a **new tool**
  (`get_card_tap_history`) so it can reason about whether this looks
  like a member's first anomaly or a repeat pattern, and whether the
  two hubs involved are places the member visits normally — real
  context a fixed risk score alone can't see. Neither of these agent
  capabilities executes anything automatically: the rebalancing
  capability recommends a candidate hub for a human dispatcher (see the
  note below on why it stops there), and the anomaly review only
  refines the *explanation and recommended severity* of an event a
  human is already going to look at.

**HUB-003 Hub Capacity & Utilization Analytics** — throughput, dwell
time, and capacity utilization stay plain arithmetic over real
touchpoint data, the same principle as dms-svc's P&L board: no AI
reasoning belongs on a metrics surface where the number itself is the
product and needs to be exactly reproducible. What's new: this data now
actually reaches an agent — `evaluate_hub_rebalancing` reads the real
network snapshot (via a new tool, not a placeholder) to reason about
network-wide redistribution, closing the loop I'd previously left open.

## The fleet-svc/hub-svc reconciliation is still open, on purpose

The Fleet Intelligence Agent's hub-rebalancing capability recommends a
candidate hub — it deliberately does **not** call
`fleet_tools.propose_transfer` to execute that recommendation, because
doing so would require the fleet-svc/hub-svc hub-id reconciliation
flagged earlier in this README as a real, distinct piece of follow-up
work. Guessing at an ID mapping between two independently-seeded UUID
spaces to make the demo look more automated would be exactly the kind
of thing to avoid — the recommendation is real and useful to a human
dispatcher today; wiring it to an actual automatic transfer is the
honest next step once that reconciliation exists, not something to
paper over now.

## A deliberate architectural seam, stated plainly

fleet-svc already has its own `Hub` record (FLT-003) — deliberately
thin (id/name/region), just an FK target for `current_hub_id` and
`TransferOrder` staging logic that's already tested and working. This
service's `Hub` is the richer HUB-001 administrative profile (type,
geo coordinates, capacity, operating hours, amenities) the enterprise
spec actually calls for. In a real deployment the two would converge —
hub-svc becomes the record of truth and fleet-svc's table becomes a
synced cache — but that reconciliation is a real, distinct piece of
follow-up work, not something to gloss over by quietly duplicating IDs
and hoping they stay in sync. Stated here so it isn't a surprise later.

## Tests

```bash
python -m pytest tests/ -v   # 33 passing, 0 failures
ruff check .                 # 0 lint errors
```
