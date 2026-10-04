# VOYQI Dealer Center
## Installation Guide

The Dealer Center is a dealership management system spanning 13 capability areas — rooftop/franchise administration, retail inventory and merchandising, the sales desk and deal manager, F&I and compliance, lender access and credit decisioning, trade-in appraisal, reconditioning, fixed-operations service and repair orders, parts inventory, loaner vehicles, P&L reporting, franchise compliance, and a platform-wide compliance review queue.

The system has three parts, all required for full functionality:

1. **`dms-svc`** — the backend of record for every dealership record (rooftops, vehicles, deals, credit applications, appraisals, repair orders, parts, loaners, franchise records, P&L snapshots) and every deterministic business rule (gross-floor pricing, aging thresholds, reorder points, OFAC screening, the Red Flags Rule override gate, stolen-vehicle holds).
2. **`ai-orchestration-svc`** — the reasoning layer. Every autonomous evaluation (deal desking, lending routing, aging sweeps, service dispatch, parts reorder, loaner-overdue flagging, franchise remediation, dealer-performance intervention, trade appraisal, retail pricing) is exposed here as its own endpoint, backed by an agent that calls into `dms-svc` to read and write the underlying records.
3. **`voyqi-dealer-center.html`** — a single-file browser frontend with no build step. It holds no business logic of its own; every number and every autonomous action it shows comes from a live call to one of the two services above.

---

## Step 1 — Prerequisites

- Python 3.12+
- `pip` with network access to PyPI (or a local mirror)
- A PostgreSQL instance for any non-local environment (SQLite is supported only for local development and testing)
- A modern browser to serve `voyqi-dealer-center.html` from — no Node.js, bundler, or build step is required for the frontend itself

---

## Step 2 — Install and start `dms-svc`

```bash
cd dms-svc
pip install -r requirements.txt --break-system-packages
```

Set the database connection before starting the service:

```bash
export DMS_DATABASE_URL="postgresql+asyncpg://user:password@host:5432/dms"
# or, for local development only:
export DMS_DATABASE_URL="sqlite+aiosqlite:///./dms.db"
```

Apply migrations:

```bash
alembic upgrade head
```

Start the service:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8008
```

Confirm it's running:

```bash
curl http://localhost:8008/health
# {"status":"ok","service":"dms-svc"}
```

### CORS

`dms-svc` accepts cross-origin requests from any origin by default, since the frontend calls it directly from the browser with no server-side proxy in front of it. Before exposing this to production traffic, narrow `allow_origins` in `src/main.py` from `["*"]` to the specific origin the Dealer Center is actually served from.

---

## Step 3 — Install and start `ai-orchestration-svc`

```bash
cd ai-orchestration-svc
pip install -r requirements.txt --break-system-packages
```

Configure its own database and point it at the running `dms-svc`:

```bash
export AI_ORCH_DATABASE_URL="postgresql+asyncpg://user:password@host:5432/ai_orchestration"
export AI_ORCH_DMS_SVC_BASE_URL="http://dms-svc:8008"   # or http://localhost:8008 for local development
```

If live model-backed reasoning is desired (as opposed to the deterministic fallback every agent also supports), set:

```bash
export ANTHROPIC_API_KEY="sk-..."
```

Apply migrations and start the service:

```bash
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8020
```

Confirm it's running and can reach `dms-svc`:

```bash
curl http://localhost:8020/health
# {"status":"ok","service":"ai-orchestration-svc"}
```

The two agents this deployment exercises — the **Dealer Intelligence Agent** and the **Pricing Intelligence Agent** — expose the following endpoints, each backed by a real call into `dms-svc`:

| Endpoint | Capability |
|---|---|
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-license-renewals` | Auto-files renewals inside the 60-day window |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-aging-sweep` | Auto-marks-down inventory past the aging threshold |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-deal-desk` | Desks and approves a deal in one pass |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-lending` | Routes a credit application to lenders; OFAC blocks structurally, before routing is ever reached |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-service-dispatch` | Dispatches a repair order and estimates duration |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-parts-reorder` | Places vendor orders at or below reorder point |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-loaner-overdue` | Flags loaners past the billing-handoff threshold |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-franchise-remediation` | Opens remediation for non-compliant rooftops |
| `POST /api/v1/ai/dms/dealer-intelligence/evaluate-dealer-performance` | The P&L capstone — computes a verdict per rooftop and applies an accelerated markdown sweep where warranted |
| `POST /api/v1/ai/dms/evaluate-trade-appraisal` | Values a trade-in and extends an offer |
| `POST /api/v1/ai/dms/evaluate-retail-pricing` | Recommends a retail price adjustment |
| `POST /api/v1/ai/dms/evaluate-max-bid` | Recommends a maximum acquisition bid |

Every one of these is called with an `X-Tenant-Id` header identifying the dealer group, matching the same header `dms-svc` itself requires.

### CORS

Apply the same CORS configuration described for `dms-svc` above — the frontend's AI Studio and every autonomous-action button call `ai-orchestration-svc` directly from the browser.

---

## Step 4 — Deploy the frontend

`voyqi-dealer-center.html` is a static file. It can be served from any web server, a CDN, or opened directly from disk for local testing.

Before serving it, set the two API base URLs it will call. Either edit the two constants near the top of the inline `<script>` block:

```javascript
var DMS_API_BASE = window.DMS_API_BASE || 'http://localhost:8008';
var AI_ORCH_API_BASE = window.AI_ORCH_API_BASE || 'http://localhost:8020';
```

or, without editing the file, set `window.DMS_API_BASE` and `window.AI_ORCH_API_BASE` before the page's own script runs — for example, via a small inline `<script>` tag injected by whatever serves the file:

```html
<script>
  window.DMS_API_BASE = 'https://dms.yourdomain.com';
  window.AI_ORCH_API_BASE = 'https://ai-orchestration.yourdomain.com';
</script>
```

Serve the file:

```bash
python3 -m http.server 8080
# open http://localhost:8080/voyqi-dealer-center.html
```

On load, the page calls both services to populate the dashboard and every module. If either service is unreachable, the page surfaces a specific, visible error banner naming which service and why — it does not fail silently or fall back to placeholder data.

---

## Step 5 — Verify the installation

With both services running and the frontend pointed at them, walk through this sequence once to confirm every layer is actually connected, not just running:

1. **Rooftop Directory** — add a rooftop. Confirm it appears in the table immediately.
2. **Retail Inventory** — stock in a vehicle against that rooftop. Confirm it appears, then open the Vehicle Inspector and request an AI price recommendation — this call goes through `ai-orchestration-svc` to the Pricing Intelligence Agent and back.
3. **Sales Desk & Deals** — open a deal against the vehicle, then click "Desk & approve (AI)". The deal should move directly to `approved` — there is no intermediate manual desking step to click through.
4. **Lender & Credit Apps** — submit a credit application against the deal, then pull the bureau. A clean bureau pull routes, decisions, and selects a winning lender automatically in the same call.
5. **Trade-in Appraisal** — request an AI valuation for a VIN. Confirm a valuation and an offer both come back in the same response.
6. **Compliance Queue** — confirm this queue is empty unless one of the three genuine security events below has actually occurred. Every other module's activity should route elsewhere, not here.

If every step above completes without an error banner, the installation is verified end-to-end.

---

## Step 6 — Understand the three human-only gates

Everything in this system executes autonomously by default. Exactly three events are structurally reserved for a human decision, and none of them have an override that bypasses the gate — the gate is enforced inside `dms-svc` itself, not in the frontend, so no client-side change can work around it:

1. **OFAC sanctions match** — a credit application matching the OFAC list is permanently blocked. `pull_bureau` returns immediately on a match, before any lender routing is attempted.
2. **FTC Red Flags Rule identity-theft indicators** — a flagged deal requires a real, non-empty, human-typed override reason before it can proceed. An empty or missing reason is rejected outright.
3. **Stolen-vehicle registry match** — an appraisal on a VIN matching the stolen-vehicle registry is held at `security_hold`. There is no valuation, no offer, and no path forward from that status.

These three feed the **Compliance Queue**, which is intentionally the only page in the system where a pending human decision should ever appear.

---

## Step 7 — Production deployment checklist

- [ ] `DMS_DATABASE_URL` and `AI_ORCH_DATABASE_URL` both point at real PostgreSQL instances, not SQLite
- [ ] `ANTHROPIC_API_KEY` is set on `ai-orchestration-svc` if live model-backed reasoning is required; each agent's deterministic fallback remains fully functional without it
- [ ] `AI_ORCH_DMS_SVC_BASE_URL` resolves correctly from inside `ai-orchestration-svc`'s own network — verify with a request from within that service's environment, not just from a developer machine
- [ ] CORS `allow_origins` on both services is narrowed from `["*"]` to the exact origin serving `voyqi-dealer-center.html`
- [ ] `window.DMS_API_BASE` and `window.AI_ORCH_API_BASE` in the deployed frontend point at the production hostnames, not `localhost`
- [ ] `X-Tenant-Id` is populated correctly for the dealer group this deployment serves, consistently across every request the frontend makes
- [ ] A monitoring alert exists on the Compliance Queue's pending count — since only genuine security events reach it, a sudden spike is meaningful and should reach a human quickly
- [ ] Migrations for both services have been applied against the production database before any traffic is routed to it

---

## Troubleshooting

**The page loads but every module shows "Cannot reach dms-svc" / "Cannot reach ai-orchestration-svc."**
The corresponding service isn't running, isn't reachable from the browser's network path, or CORS is rejecting the request before it's ever sent (check the browser console — a CORS rejection shows there, not as an HTTP error). Confirm `curl <base_url>/health` succeeds from the same network the browser is on, not just from the server running the service.

**An AI action returns a generic error instead of a specific one.**
Check `ai-orchestration-svc`'s own logs first — a 404 there typically means the referenced deal, appraisal, or credit application ID doesn't exist in `dms-svc`, most often because the two services are pointed at different databases or different tenants.

**A deal, appraisal, or repair order created through the UI doesn't appear after a page refresh.**
Every record in this system is persisted in `dms-svc` and reloaded fresh on every page navigation — nothing is held only in the browser's memory. If a record disappears after a refresh, confirm the create call actually returned a success response rather than being caught by the page's own error handling, and confirm `DMS_DATABASE_URL` points at a persistent database rather than an in-memory or ephemeral one.
