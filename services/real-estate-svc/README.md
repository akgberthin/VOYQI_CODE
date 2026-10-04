# real-estate-svc

Real Estate panel backend — hub property portfolio: occupancy, lease
cost, maintenance, and utilization rollups across every hub/office/
warehouse property. Part of VOYQI's Layer 2 (Admin-Panel-Backing)
services, port `8034`.

Same "Portfolio Intelligence" grouping as `executive-svc` and
`management-svc` — reporting/rollups only, tier 1, no AI agent, no
approval-gated action, no frontend.

## Data model

- **Property** — a registered hub/office/warehouse.
- **PropertyRollupSnapshot** — one metrics blob for one property, one
  domain (`occupancy` / `lease_cost` / `maintenance` / `utilization`).
- **PropertyKPIThreshold** / **PropertyKPIAlert** — same threshold-watch
  pattern as the rest of the group.

## API

All endpoints require `X-Tenant-Id`.

| Method | Path |
|---|---|
| `POST` / `GET` | `/api/v1/real-estate/properties` |
| `DELETE` | `/api/v1/real-estate/properties/{id}` |
| `POST` / `GET` | `/api/v1/real-estate/rollups` |
| `GET` | `/api/v1/real-estate/dashboard/summary?metric_domain=...` |
| `GET` | `/api/v1/real-estate/dashboard/trend?property_key=...&metric_domain=...` |
| `POST` / `GET` | `/api/v1/real-estate/thresholds` |
| `DELETE` | `/api/v1/real-estate/thresholds/{id}` |
| `GET` | `/api/v1/real-estate/alerts?status=open` |
| `POST` | `/api/v1/real-estate/alerts/{id}/resolve` |

## Run / Test

```bash
pip install -r requirements.txt --break-system-packages
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8034
pytest
```
