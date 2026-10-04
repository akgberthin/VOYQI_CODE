# capital-finance-svc

Capital & Finance panel backend — funding source registry (debt,
equity, credit lines) with balance, utilization, cost-of-capital, and
covenant-headroom rollups. Part of VOYQI's Layer 2 (Admin-Panel-Backing)
services, port `8028`.

Same "Portfolio Intelligence" grouping as `executive-svc`,
`management-svc`, and `real-estate-svc` — reporting/rollups only, tier
1, no AI agent, no frontend. A covenant-headroom breach raises an
alert for a human to review — it is not itself a financing decision;
actual capital allocation or financing decisions are explicitly out of
this service's scope per
`docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §4.

## Data model

- **FundingSource** — a registered debt facility, equity injection, or
  credit line.
- **CapitalRollupSnapshot** — one metrics blob per funding source, one
  domain (`balance` / `utilization` / `cost_of_capital` /
  `covenant_headroom`).
- **CapitalKPIThreshold** / **CapitalKPIAlert** — same threshold-watch
  pattern as the rest of the group.

## API

All endpoints require `X-Tenant-Id`.

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/capital-finance/funding-sources` |
| `DELETE` | `/api/v1/capital-finance/funding-sources/{id}` |
| `POST` / `GET` | `/api/v1/capital-finance/rollups` |
| `GET` | `/api/v1/capital-finance/dashboard/summary?metric_domain=...` |
| `GET` | `/api/v1/capital-finance/dashboard/trend?funding_source_key=...&metric_domain=...` |
| `POST` / `GET` | `/api/v1/capital-finance/thresholds` |
| `DELETE` | `/api/v1/capital-finance/thresholds/{id}` |
| `GET` | `/api/v1/capital-finance/alerts?status=open` |
| `POST` | `/api/v1/capital-finance/alerts/{id}/resolve` |

## Run / Test

```bash
pip install -r requirements.txt --break-system-packages
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8028
pytest
```
