# management-svc

Management (shared services) panel backend — cost-center registry,
operational rollups, dashboard summaries, and KPI threshold alerts for
shared services (IT, facilities, procurement, shared-staff pools) that
serve more than one subsidiary. Part of VOYQI's Layer 2
(Admin-Panel-Backing) services, port `8033`.

No frontend ships with this service. Every endpoint is also browsable
via the auto-generated Swagger UI at `/docs` once running.

## Scope

Same "Portfolio Intelligence" grouping as `executive-svc`
(`docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §4): reporting and
rollups only, tier 1 across the board, no AI agent, no approval-gated
action.

Where `executive-svc` rolls up external business-line performance
(revenue, fleet, workforce, marketing) across subsidiaries,
`management-svc` rolls up internal shared-services operations (spend,
headcount, utilization, SLA) across cost centers. It never reads
another service's database directly — other services or an admin tool
push snapshots in.

## Data model

- **CostCenter** — a registered shared-services cost center (`it` /
  `facilities` / `procurement` / `shared_staff` / `other`).
- **OpsRollupSnapshot** — one reported metrics blob for one cost center,
  one metric domain (`spend` / `headcount` / `utilization` / `sla`), one
  period.
- **OpsKPIThreshold** — a `gte`/`lte` rule against one metric key.
- **OpsKPIAlert** — auto-raised the instant a snapshot breaches an
  active threshold; resolved manually once reviewed.

## API

All endpoints require an `X-Tenant-Id` header.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/management/cost-centers` | Register a cost center |
| `GET` | `/api/v1/management/cost-centers` | List cost centers, optionally filtered by `category` |
| `DELETE` | `/api/v1/management/cost-centers/{id}` | Deactivate a cost center |
| `POST` | `/api/v1/management/rollups` | Ingest an ops rollup snapshot (also evaluates thresholds) |
| `GET` | `/api/v1/management/rollups` | List snapshots, filtered by `cost_center_key` / `metric_domain` |
| `GET` | `/api/v1/management/dashboard/summary?metric_domain=spend` | Cross-cost-center totals from each center's latest snapshot |
| `GET` | `/api/v1/management/dashboard/trend?cost_center_key=...&metric_domain=...` | Ordered snapshot history for one cost center |
| `POST` | `/api/v1/management/thresholds` | Define a KPI threshold |
| `GET` | `/api/v1/management/thresholds` | List thresholds |
| `DELETE` | `/api/v1/management/thresholds/{id}` | Deactivate a threshold |
| `GET` | `/api/v1/management/alerts?status=open` | List KPI alerts |
| `POST` | `/api/v1/management/alerts/{id}/resolve` | Mark an alert resolved |

Every ingested snapshot and every triggered alert publishes a Kafka
event (`management.snapshot.ingested`, `management.kpi_alert.triggered`).

## Run locally

```bash
pip install -r requirements.txt --break-system-packages
export MGMT_DATABASE_URL="sqlite+aiosqlite:///./management.db"
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8033
```

## Test

```bash
pytest
```
