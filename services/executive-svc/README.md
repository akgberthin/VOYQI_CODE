# executive-svc

Executive Command Center backend — cross-subsidiary rollups, dashboard
summaries, and KPI threshold alerts. Part of VOYQI's Layer 2
(Admin-Panel-Backing) services, port `8025`.

No frontend ships with this service. It's a pure backend for another
team's integration layer to call — every endpoint below is also browsable
via the auto-generated Swagger UI at `/docs` once the service is running.

## Scope

Per the platform's "Portfolio Intelligence" grouping
(`docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §4): reporting and
rollups only. There is no AI agent and no approval-gated action here —
every endpoint is tier 1, informational.

`executive-svc` never reads another service's database directly
(database-per-service, no cross-service joins). Other services — or a
batch job, or an admin tool — push periodic rollups in via
`POST /api/v1/executive/rollups`; this service aggregates and alerts on
what it's given.

## Data model

- **RollupSnapshot** — one reported metrics blob for one subsidiary, one
  metric domain (`revenue` / `fleet` / `workforce` / `marketing`), one
  period.
- **KPIThreshold** — a `gte`/`lte` rule against one metric key within a
  domain.
- **KPIAlert** — auto-raised the instant a snapshot breaches an active
  threshold; resolved manually once reviewed.

## API

All endpoints require an `X-Tenant-Id` header.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/executive/rollups` | Ingest a rollup snapshot (also evaluates thresholds) |
| `GET` | `/api/v1/executive/rollups` | List snapshots, optionally filtered by `subsidiary_key` / `metric_domain` |
| `GET` | `/api/v1/executive/dashboard/summary?metric_domain=revenue` | Cross-subsidiary totals from each subsidiary's latest snapshot |
| `GET` | `/api/v1/executive/dashboard/trend?subsidiary_key=...&metric_domain=...` | Ordered snapshot history for one subsidiary |
| `POST` | `/api/v1/executive/thresholds` | Define a KPI threshold |
| `GET` | `/api/v1/executive/thresholds` | List thresholds for the tenant |
| `DELETE` | `/api/v1/executive/thresholds/{id}` | Deactivate a threshold |
| `GET` | `/api/v1/executive/alerts?status=open` | List KPI alerts |
| `POST` | `/api/v1/executive/alerts/{id}/resolve` | Mark an alert resolved |

Every ingested snapshot and every triggered alert also publishes a Kafka
event (`executive.snapshot.ingested`, `executive.kpi_alert.triggered`),
matching the platform-wide rule that every state change publishes an
event.

## Run locally

```bash
pip install -r requirements.txt --break-system-packages
export EXEC_DATABASE_URL="sqlite+aiosqlite:///./executive.db"   # or Postgres in prod
alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8025
```

## Test

```bash
pytest
```
