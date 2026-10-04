# insurance-risk-svc

Compliance & Risk Intelligence — insurance policy registry and
deterministic risk scoring. Port `8029`.

Lean foundation per the client's phased plan: this is the deployable
service scaffold (Phase 1); deeper modules (claims processing,
underwriting workflows, reinsurance) are Phase 2.

## Autonomy tier

Per `docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §4 (Compliance &
Risk Intelligence group): anomaly flagging and policy simulation are
tier 1. Every endpoint here is exactly that — scoring and flagging,
never a coverage, premium, or status change. `abac.policy_grant_apply`
(tier 3, the other action in this group) belongs to
`security-compliance-svc`, not here.

## Data model

- **RiskPolicy** — a registered insured policy.
- **RiskAssessment** — one deterministic risk-score run against a
  policy: coverage amount (capped, log-ish scaling), a policy-type
  multiplier, and a claims-history component, combined into a 0–100
  score and a `low` / `elevated` / `high` band. Every component is
  named in `src/services/risk_engine.py`'s config, not buried in a
  function body.

## API

All endpoints require `X-Tenant-Id`.

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/insurance-risk/policies` |
| `GET` | `/api/v1/insurance-risk/policies/{id}` |
| `POST` | `/api/v1/insurance-risk/policies/{id}/status` |
| `POST` / `GET` | `/api/v1/insurance-risk/policies/{id}/assessments` |

## Run / Test

```bash
pip install -r requirements.txt --break-system-packages
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8029
pytest
```
