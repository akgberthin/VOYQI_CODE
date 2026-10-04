# originations-svc

Sales & Originations Intelligence — loan/financing application intake,
deterministic lead scoring, and the platform's credit-decision gate.
Port `8036`.

Lean foundation per the client's phased plan: this is the deployable
service scaffold (Phase 1); deeper modules (lender matching, deal
structuring, e-contracting) are Phase 2.

## Autonomy tiers

Per `docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §4
(Sales & Originations Intelligence group):

- **`lead.score_and_route`** — tier 1. `POST /applications/{id}/scores`
  runs a deterministic score (FICO + income-to-requested-amount ratio)
  and auto-executes; it never itself approves or declines financing.
- **`credit.decision`** — tier 3, hard-coded, no exceptions. *"This is
  a fair-lending requirement."* No risk score and no threshold apply.
  `src/services/credit_decision_engine.decide` is the only place a
  `CreditDecision` can leave `pending`, and it refuses without a named
  `accountable_role` — **for a decline exactly as much as an
  approval**, since an adverse credit action needs accountable
  documentation under fair-lending law just as much as an approval
  does.

This mirrors the same gate already wired into `ai-orchestration-svc`'s
`dealer_intelligence_agent.evaluate_lending` for DMS-originated
lending — this service is the standalone, cross-subsidiary home for
the same discipline.

## Data model

- **LoanApplication** — the application record and its lifecycle
  (`draft` → `scored` → `pending_decision` → `approved`/`declined`).
- **ApplicationScore** — one scoring run (tier 1).
- **CreditDecision** — the tier-3 gate itself.

## API

All endpoints require `X-Tenant-Id`.

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/originations/applications` |
| `GET` | `/api/v1/originations/applications/{id}` |
| `POST` / `GET` | `/api/v1/originations/applications/{id}/scores` |
| `POST` / `GET` | `/api/v1/originations/credit-decisions` |
| `POST` | `/api/v1/originations/credit-decisions/{id}/decide` |

## Run / Test

```bash
pip install -r requirements.txt --break-system-packages
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8036
pytest
```
