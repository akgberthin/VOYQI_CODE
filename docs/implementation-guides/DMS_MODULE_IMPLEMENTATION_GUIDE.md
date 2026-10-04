# Dealer Management System (DMS) Module
## Implementation Guide

A step-by-step guide to implementing the DMS module: a standalone backend service (`dms-svc`) plus an orchestration layer in `ai-orchestration-svc`. Covers all 13 dealership capability areas — rooftop licensing, retail inventory, sales desk, F&I/compliance, lending, trade-in appraisal, recon, service, parts, loaners, franchise compliance, P&L, and a security-hold queue.

**Governing principle:** every capability executes autonomously — the decision and the action happen in the same call, with no pending/approval state in between. Exactly three fields on the entire schema are human-only, because they're genuine security matters (OFAC sanctions, identity-theft indicators under the FTC Red Flags Rule, stolen-vehicle registry holds) rather than business judgment calls. Everything else — pricing, deal approval, lending decisions, dispatch, reordering, remediation — is autonomous by design.

---

## Step 1 — Scaffold the service

Create `dms-svc` with the same shape as any other service on the platform:

```
services/dms-svc/
├── requirements.txt
├── alembic.ini
├── pytest.ini
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── src/
│   ├── main.py
│   ├── core/          (config, database, security, kafka_events)
│   ├── models/         (single __init__.py holding every table)
│   ├── services/       (one engine module per capability area)
│   └── api/             (one router module per capability area)
└── tests/
    ├── conftest.py
    └── test_*.py        (one file per capability area)
```

`requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.32
sqlalchemy>=2.0
alembic>=1.13
aiosqlite>=0.20
asyncpg>=0.29
pydantic>=2.9
pydantic-settings>=2.5
httpx>=0.27
aiokafka>=0.11
pytest>=8.3
pytest-asyncio>=0.24
ruff>=0.7
```

`pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

`alembic.ini`:
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = driver://user:pass@localhost/dbname
```

---

## Step 2 — Core infrastructure

### `src/core/database.py`

Async SQLAlchemy engine plus a cross-dialect UUID type (`CHAR(36)` on SQLite for tests, native `UUID` on PostgreSQL in production) so model code never branches on dialect:

```python
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import CHAR, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        return str(value) if isinstance(value, uuid.UUID) else str(uuid.UUID(str(value)))

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

### `src/core/security.py`

Every request carries a tenant header — no default, no fallback:

```python
from fastapi import Header, HTTPException


async def get_tenant_id(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")
    return x_tenant_id
```

### `src/core/config.py`

Every threshold the 13 capabilities need, as named settings — never a magic number buried in a function body:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DMS_", extra="ignore")

    service_name: str = "dms-svc"
    port: int = 8008
    database_url: str = "sqlite+aiosqlite:///./dms.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"
    ai_initiated_header_value: str = "dealer_intelligence_agent"

    aging_markdown_threshold_days: int = 60
    aging_markdown_pct_per_day: float = 0.005
    aging_markdown_cap_pct: float = 0.15
    min_gross_floor_pct: float = 0.03
    trade_offer_pct_of_valuation: float = 0.93
    lending_approve_fico_floor: int = 620
    lending_counter_fico_floor: int = 580
    lending_prime_fico_floor: int = 700
    service_loaner_duration_threshold_days: int = 1
    loaner_extended_days_threshold: int = 5
    license_renewal_window_days: int = 60
    intervention_aging_threshold_days: int = 30
    intervention_markdown_cap_pct: float = 0.10
    intervention_markdown_pct_per_day: float = 0.004
    parts_reorder_minimum_qty: int = 20
    parts_reorder_multiplier: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### `src/core/kafka_events.py`

Standard fire-and-forget publisher: a real call in production, logs and continues on failure so a Kafka outage never blocks a request:

```python
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dms-svc.events")
_producer = None


async def get_producer():
    global _producer
    if _producer is None:
        from aiokafka import AIOKafkaProducer
        from src.core.config import get_settings
        candidate = AIOKafkaProducer(bootstrap_servers=get_settings().kafka_bootstrap_servers)
        await candidate.start()
        _producer = candidate
    return _producer


async def publish_event(topic: str, event_type: str, tenant_id: str, payload: dict[str, Any]) -> None:
    envelope = {"event_type": event_type, "tenant_id": tenant_id, "occurred_at": datetime.now(timezone.utc).isoformat(), "payload": payload}
    try:
        producer = await get_producer()
        await producer.send_and_wait(topic, json.dumps(envelope).encode("utf-8"))
    except Exception:
        logger.info("event_publish_fallback topic=%s event=%s", topic, event_type)
```

---

## Step 3 — Data model

Build one `src/models/__init__.py` with every table. Group by capability area; use `str, enum.Enum` for every status field so values serialize cleanly through Pydantic. The three security fields get called out explicitly in their docstrings — this is the schema-level enforcement point, not just a comment:

| Capability | Tables | Security-relevant field |
|---|---|---|
| Rooftop directory | `Rooftop` | — |
| Retail inventory | `Vehicle`, `BookValueQuote`, `VehicleHistoryReport`, `PricingRecommendation` | — |
| Sales desk | `Deal` | — |
| F&I / compliance | `Lender`, `CreditApplication`, `LenderSubmission`, `AdverseActionNotice`, `RedFlagCheck` | `CreditApplication.ofac_match`, `RedFlagCheck.overridden_by`/`override_reason` |
| Trade-in appraisal | `Appraisal` | `Appraisal.status = SECURITY_HOLD` |
| Recon | `ReconOrder`, `ReconWorkItem` | — |
| Service | `RepairOrder` | — |
| Parts | `Part` | — |
| Loaners | `Loaner` | — |
| Franchise compliance | `FranchiseCompliance` | — |
| P&L capstone | `PLSnapshot`, `PerformanceEvaluation` | — |
| Compliance queue | `ComplianceHold` | the entire table |

Key column choices worth calling out:

- **Guard columns for idempotent sweeps.** `Vehicle.last_markdown_days` and `Part.last_reorder_stock_qty` record the value a sweep last acted on. A sweep function checks this before writing — if the value hasn't changed since the last pass, skip. Without this, calling a sweep endpoint twice in a row would double-apply a discount or double-order stock.
- **`Appraisal.status` includes `SECURITY_HOLD` as a first-class enum value**, not a boolean flag layered on top of `PENDING`/`OFFERED`. This makes "can this appraisal be valued?" a single status check everywhere in the codebase, rather than a compound condition someone could get wrong once and forget everywhere else.
- **`ComplianceHold.type` is a closed enum of exactly three values** (`ofac`, `red_flag`, `stolen_vehicle`). Don't make this an open string field — a closed enum is what lets a test assert "this table only ever contains these three types" as a real invariant, not a hopeful comment.

---

## Step 4 — Build the engine layer

One module per capability area in `src/services/`. Every engine function that represents a business decision does two things in the same call: computes the outcome, and writes it. There is no `record_recommendation()` followed by a separate `apply_recommendation()` — that split is exactly the pattern to avoid, because a recommendation that isn't automatically applied becomes a permanent dead end no one ever clicks through.

### 4.1 — Rooftop licensing (`rooftop_engine.py`)

```python
async def evaluate_and_file_renewals(db, tenant_id) -> list[Rooftop]:
    settings = get_settings()
    filed = []
    for rooftop in await list_rooftops(db, tenant_id):
        if rooftop.renewal_filed:
            continue
        if days_until_expiry(rooftop) < settings.license_renewal_window_days:
            rooftop.renewal_filed = True
            filed.append(rooftop)
    if filed:
        await db.commit()
    return filed
```

One design note: don't build cross-location inventory transfer into this module if each location is licensed for a single brand — a vehicle can only ever be sold where its brand is licensed, so there's no valid transfer target to compute in the first place. Confirm this constraint against the real business rules before adding a capability that looks obviously useful; it may not have a legal path to execute at all.

### 4.2 — Retail inventory (`inventory_engine.py`)

Two related but distinct actions: `apply_price` (the terminal action, called by everything — manual re-price, an aging sweep, an agent recommendation) and `evaluate_aging_sweep` (the batch job that finds vehicles past the aging threshold and calls `apply_price` for each). Anchor the price off real book-value data when it exists, falling back to the vehicle's own current price:

```python
async def apply_price(db, tenant_id, vehicle_id, price_usd, reasoning) -> Vehicle:
    vehicle = await get_vehicle(db, tenant_id, vehicle_id)
    vehicle.price_usd = price_usd
    db.add(PricingRecommendation(tenant_id=tenant_id, vehicle_id=vehicle_id, price_usd=price_usd, reasoning=reasoning))
    await db.commit()
    return vehicle


async def evaluate_aging_sweep(db, tenant_id) -> list[Vehicle]:
    marked_down = []
    for vehicle in await list_vehicles(db, tenant_id):
        if vehicle.status != VehicleStatus.IN_STOCK or vehicle.days_in_inventory < settings.aging_markdown_threshold_days:
            continue
        if vehicle.last_markdown_days == vehicle.days_in_inventory:
            continue  # already swept at this aging value — the idempotency guard
        anchor = (await get_book_value_average(db, tenant_id, vehicle.vin))["average_retail_value_usd"] or vehicle.price_usd
        new_price, pct = compute_aging_markdown(anchor, vehicle.days_in_inventory)
        vehicle.price_usd = new_price
        vehicle.last_markdown_days = vehicle.days_in_inventory
        marked_down.append(vehicle)
    if marked_down:
        await db.commit()
    return marked_down
```

Title-brand disclosure (`pull_vhr`) belongs in this module too. Draft the disclosure text and sync the listing in the same call the history report is pulled — a title-brand disclosure is a consumer-protection obligation, not a security matter, so it doesn't need a human-typed justification the way the three genuinely security-relevant fields do.

### 4.3 — Sales desk (`deal_engine.py`)

`desk_and_approve` computes the desk price, checks it against a minimum gross-margin floor, and — if the price misses the floor — counters it upward to exactly clear the floor, in the same call:

```python
async def desk_and_approve(db, tenant_id, deal_id) -> Deal:
    deal = await get_deal(db, tenant_id, deal_id)
    vehicle = await get_vehicle_for_deal(db, deal)
    desk_price = round(vehicle.price_usd * 0.97, 2)
    gross_pct = (desk_price - vehicle.cost_usd) / vehicle.cost_usd

    deal.price_usd = desk_price if gross_pct >= settings.min_gross_floor_pct else round(vehicle.cost_usd * (1 + settings.min_gross_floor_pct), 2)
    deal.status = DealStatus.APPROVED  # always — no intermediate state a human needs to act on
    await db.commit()
    return deal
```

### 4.4 — F&I and compliance (`fi_engine.py`)

Two functions with opposite designs, deliberately:

```python
async def auto_issue_adverse_action(db, tenant_id, deal_id, reason) -> AdverseActionNotice:
    notice = AdverseActionNotice(tenant_id=tenant_id, deal_id=deal_id, reason=reason, deadline=..., status=AdverseActionStatus.ISSUED)
    db.add(notice)
    await db.commit()
    return notice


async def override_red_flag(db, tenant_id, check_id, overridden_by, reason) -> RedFlagCheck:
    if not reason or not reason.strip():
        raise MissingOverrideReasonError("a human-typed override reason is required")
    check = await get_red_flag_check(db, tenant_id, check_id)
    check.overridden_by = overridden_by
    check.override_reason = reason.strip()
    await db.commit()
    return check
```

An adverse-action notice is a legal deadline, not a judgment call — it's issued unconditionally. A red-flag override is an identity-theft indicator under the FTC Red Flags Rule — it structurally cannot proceed without a real, non-empty, human-typed reason. Build both the automatic path and the hard gate in the same file to make the contrast visible to the next person reading it.

### 4.5 — Lending (`lending_engine.py`)

The one place a security check gates an entire downstream chain. Check it first, and return before anything else runs:

```python
async def pull_bureau(db, tenant_id, app_id, bureau, fico, ofac_match) -> CreditApplication:
    app = await get_credit_application(db, tenant_id, app_id)
    app.bureau, app.fico, app.ofac_match = bureau, fico, ofac_match

    if ofac_match:
        app.status = CreditApplicationStatus.BLOCKED
        await db.commit()
        return app  # routing is never reached — not gated, structurally absent

    await db.commit()
    await auto_route_to_lenders(db, tenant_id, app_id)
    return app
```

`auto_route_to_lenders` picks lenders by FICO tier (prime credit → every lender for rate competition; subprime → a smaller subset), then immediately decisions every submission and selects the lowest-APR winner among cleared offers — three steps, one call, no manual submission or approval anywhere in the chain. A `DECLINED` decision inside `auto_decide_submission` calls `fi_engine.auto_issue_adverse_action` directly.

### 4.6 — Trade-in appraisal (`appraisal_engine.py`)

The security check for this module — a stolen-vehicle registry lookup — belongs at the API layer, not inside this engine, because it's a plain registry lookup, not a valuation. The engine itself just enforces that a held appraisal can never be valued:

```python
async def apply_valuation_and_offer(db, tenant_id, appraisal_id, valuation_usd) -> Appraisal:
    appraisal = await get_appraisal(db, tenant_id, appraisal_id)
    if appraisal.status == AppraisalStatus.SECURITY_HOLD:
        raise ValueError("cannot offer on an appraisal under security hold")
    appraisal.valuation_usd = valuation_usd
    appraisal.offer_usd = round(valuation_usd * settings.trade_offer_pct_of_valuation, 2)
    appraisal.status = AppraisalStatus.OFFERED
    await db.commit()
    return appraisal
```

At the API layer, the registry check runs before this function is ever called:

```python
@router.post("/find-or-create")
async def find_or_create_appraisal(body, tenant_id, db):
    appraisal = await appraisal_engine.find_or_create_appraisal(db, tenant_id, body.vin, body.mileage)
    if body.stolen_vehicle_match:
        appraisal = await appraisal_engine.set_security_hold(db, tenant_id, appraisal.id)
        await compliance_engine.create_hold(db, tenant_id, "stolen_vehicle", "...", "...", risk_score=80)
    return appraisal
```

Accepting or declining an offer is the customer's own decision — write a function that records that outcome, don't compute one.

### 4.7 — Recon (`recon_engine.py`)

Assign work items to a shop resource by category at creation time — never leave a work item unassigned waiting on a dispatcher:

```python
ASSIGNEE_BY_CATEGORY = {"mechanical": "tech-14", "detail": "detail-shop", "body": "body-shop-1", "tires": "tire-bay", "other": "shop-foreman"}

async def add_work_item(db, tenant_id, order_id, category, description, cost_usd) -> ReconWorkItem:
    item = ReconWorkItem(..., assigned_to=ASSIGNEE_BY_CATEGORY.get(category, "shop-foreman"))
    ...
```

Marking a single item complete is a technician reporting real physical work finished — write it as a direct, ungated action. But check, in the same function, whether every item on the order is now complete, and if so, advance the order automatically:

```python
async def complete_work_item(db, tenant_id, item_id) -> tuple[ReconWorkItem, ReconOrder]:
    item = await _get_item(db, item_id)
    item.status = ReconWorkItemStatus.COMPLETED
    await db.commit()

    order = await get_recon_order(db, tenant_id, item.recon_order_id)
    all_items = await get_work_items(db, tenant_id, order.id)
    if all(i.status == ReconWorkItemStatus.COMPLETED for i in all_items):
        await auto_advance_to_front_line_ready(db, tenant_id, order.id)
    return item, order
```

### 4.8 — Service (`service_engine.py`)

Estimate job duration from the description at RO-creation time, using a small keyword table, and dispatch a tech in the same call:

```python
DURATION_KEYWORDS = [
    (("transmission", "engine", "head gasket"), 3),
    (("recall", "software"), 1),
    (("brake", "suspension", "alignment"), 1),
    (("oil", "tire", "rotation", "wiper", "battery"), 0),
]

def estimate_service_duration(description: str) -> int:
    desc_lower = description.lower()
    for keywords, days in DURATION_KEYWORDS:
        if any(kw in desc_lower for kw in keywords):
            return days
    return 1  # default: assume a full day for anything unrecognized

async def create_and_dispatch_ro(db, tenant_id, ...) -> RepairOrder:
    est_days = estimate_service_duration(description)
    ro = RepairOrder(..., tech=f"tech-{random.randint(10, 89)}", estimated_duration_days=est_days)
    db.add(ro)
    await db.commit()
    if est_days >= settings.service_loaner_duration_threshold_days:
        await loaner_engine.auto_assign_loaner(db, tenant_id, ro.id, customer_name, est_days)
    return ro
```

That last call is the chain into loaners — a multi-day estimate triggers a loaner assignment in the same request, not as a follow-up step someone has to remember.

### 4.9 — Parts (`parts_engine.py`)

A reorder sweep guarded exactly like the inventory aging sweep:

```python
async def evaluate_and_reorder(db, tenant_id) -> list[Part]:
    reordered = []
    for part in await list_parts(db, tenant_id):
        if part.stock_qty > part.reorder_point:
            continue
        if part.last_reorder_stock_qty == part.stock_qty:
            continue
        part.stock_qty += max(settings.parts_reorder_minimum_qty, part.reorder_point * settings.parts_reorder_multiplier)
        part.last_reorder_stock_qty = part.stock_qty
        reordered.append(part)
    if reordered:
        await db.commit()
    return reordered
```

### 4.10 — Loaners (`loaner_engine.py`)

`auto_assign_loaner` is called directly from the service engine's dispatch step — not exposed as an independent "reserve a loaner" action for the common case. `evaluate_overdue_loaners` flags anything past the extended-duration threshold for billing handoff:

```python
async def evaluate_overdue_loaners(db, tenant_id) -> list[Loaner]:
    flagged = []
    for loaner in await list_loaners(db, tenant_id):
        if loaner.status == LoanerStatus.OUT and loaner.days_out >= settings.loaner_extended_days_threshold and not loaner.billing_flag:
            loaner.billing_flag = True
            flagged.append(loaner)
    if flagged:
        await db.commit()
    return flagged
```

### 4.11 — Franchise compliance (`franchise_engine.py`)

```python
async def evaluate_and_remediate(db, tenant_id) -> list[FranchiseCompliance]:
    opened = []
    for record in await list_franchise_records(db, tenant_id):
        if record.status == FranchiseStatus.NON_COMPLIANT and not record.remediation_opened:
            record.remediation_opened = True
            opened.append(record)
    if opened:
        await db.commit()
    return opened
```

### 4.12 — P&L capstone (`performance_engine.py`)

The capstone reads two other tables' real figures — never writes to them — and, when it acts, writes to a third table entirely:

```python
async def evaluate_dealer_performance(db, tenant_id) -> list[PerformanceEvaluation]:
    snapshots = await list_latest_snapshots(db, tenant_id)
    group_avg_gross = sum(s.gross_usd for s in snapshots) / len(snapshots)
    franchise_by_rooftop = {f.rooftop_id: f for f in await _franchise_records(db, tenant_id)}

    evaluations = []
    for snap in snapshots:
        points = 0
        if snap.gross_usd < group_avg_gross: points += 1
        if snap.turn_rate < 1.0: points += 1
        if snap.absorption_pct < 60: points += 1
        franchise = franchise_by_rooftop.get(snap.rooftop_id)
        if franchise and franchise.status != FranchiseStatus.COMPLIANT: points += 1

        verdict = PerformanceVerdict.INTERVENE if points >= 3 else (PerformanceVerdict.MONITOR if points >= 1 else PerformanceVerdict.STRONG)
        evaluations.append(PerformanceEvaluation(tenant_id=tenant_id, rooftop_id=snap.rooftop_id, verdict=verdict, points=points))
    db.add_all(evaluations)
    await db.commit()
    return evaluations


async def evaluate_and_apply_interventions(db, tenant_id) -> list[PerformanceEvaluation]:
    evaluations = await evaluate_dealer_performance(db, tenant_id)
    for evaluation in evaluations:
        if evaluation.verdict != PerformanceVerdict.INTERVENE or evaluation.intervention_applied:
            continue
        for vehicle in await _in_stock_vehicles(db, tenant_id, evaluation.rooftop_id):
            if vehicle.days_in_inventory >= settings.intervention_aging_threshold_days:
                await inventory_engine.apply_accelerated_markdown(db, tenant_id, vehicle.id, ...)
        evaluation.intervention_applied = True
    await db.commit()
    return evaluations
```

**Critical implementation detail:** `apply_accelerated_markdown` must carry the exact same `last_markdown_days` idempotency guard as the standard aging sweep in Step 4.2. `PerformanceEvaluation.intervention_applied` is a fresh row's own flag — it does not, by itself, prevent a second call to `evaluate_and_apply_interventions` from re-discounting the same vehicle. The guard that actually prevents double-discounting has to live on the `Vehicle` row itself, checked inside `apply_accelerated_markdown`. Write a test that calls the endpoint twice in a row and asserts the price is identical after both calls — this is the single easiest bug to introduce in this entire module, because it only shows up on the second call, never the first.

### 4.13 — Compliance holds (`compliance_engine.py`)

Deliberately thin — this table only ever receives writes from the three security checkpoints elsewhere (OFAC in lending, the red-flag structural gate in F&I, the stolen-vehicle check in appraisals):

```python
async def create_hold(db, tenant_id, hold_type, label, detail, risk_score) -> ComplianceHold:
    hold = ComplianceHold(tenant_id=tenant_id, type=hold_type, label=label, detail=detail, risk_score=risk_score)
    db.add(hold)
    await db.commit()
    return hold

async def resolve_hold(db, tenant_id, hold_id) -> ComplianceHold:
    hold = await _get_hold(db, tenant_id, hold_id)
    hold.resolved = True
    await db.commit()
    return hold
```

Do not add a generic "log a compliance event" function to this module that other engines could call for convenience — that's exactly how a fourth, non-security category would eventually end up in this table. Every other capability's audit trail belongs on its own table or in a Kafka event, not here.

---

## Step 5 — Build the API layer

One router per capability area, each declaring its own full path prefix (`/api/v1/dms/<area>`). Every endpoint that represents an autonomous action returns the *result* of that action, not an acknowledgment that a request was queued:

```python
router = APIRouter(prefix="/api/v1/dms/vehicles", tags=["inventory"])

@router.post("/evaluate-aging-sweep", response_model=list[VehicleOut])
async def evaluate_aging_sweep(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await inventory_engine.evaluate_aging_sweep(db, tenant_id)
```

For the security-relevant endpoints, translate engine exceptions to the right HTTP status rather than letting a generic 500 through:

```python
@router.post("/red-flag-checks/{check_id}/override", response_model=RedFlagCheckOut)
async def override_red_flag(check_id, body, tenant_id, db):
    try:
        return await fi_engine.override_red_flag(db, tenant_id, check_id, body.overridden_by, body.reason)
    except fi_engine.MissingOverrideReasonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

422 for a missing required input, 409 for an action refused because of the object's current state (e.g., valuing a security-held appraisal), 404 for a missing object.

---

## Step 6 — Assemble the application

```python
# src/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.api import (
    appraisals, compliance, deals, fi, franchise, inventory,
    lending, loaners, parts, performance, recon, rooftops, service,
)
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="dms-svc", version="1.0.0", lifespan=lifespan)
for router_module in (rooftops, inventory, deals, fi, lending, appraisals, recon, service, parts, loaners, franchise, performance, compliance):
    app.include_router(router_module.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "dms-svc"}
```

---

## Step 7 — Generate the migration

```bash
alembic revision --autogenerate -m "initial dms-svc schema"
```

**Check the generated file for a missing import before applying it.** Autogenerate sometimes emits a `GUID()` column type reference without adding the corresponding `import src.core.database` at the top of the migration file — add it manually if so:

```python
from alembic import op
import sqlalchemy as sa
import src.core.database   # <- add this if autogenerate omitted it
```

Apply and confirm every table lands:

```bash
alembic upgrade head
python3 -c "
import sqlite3
conn = sqlite3.connect('dms.db')
print(sorted(r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'alembic%'\")))
"
```

---

## Step 8 — Write the test suite

`tests/conftest.py` — a StaticPool in-memory SQLite engine shared between a raw `db_session` fixture (for direct engine-level assertions) and an `AsyncClient` fixture (for full HTTP-level assertions):

```python
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from src.core.database import Base, get_db
from src.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite://"

@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(db_engine):
    async with async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)() as session:
        yield session

@pytest_asyncio.fixture
async def client(db_engine):
    async def override_get_db():
        async with async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)() as session:
            yield session
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

TENANT_HEADERS = {"X-Tenant-Id": "voyqi-holdings"}
```

For each capability area, write at minimum:

1. **The happy path** — the autonomous action executes and produces the expected state change.
2. **The idempotency guard** — call the sweep/action twice; assert the second call is a no-op (parts, aging sweep, the P&L capstone's intervention). This is the test category most likely to catch a real bug, as noted in Step 4.12.
3. **For the three security-relevant capabilities specifically**, an explicit test that the block is total: zero lender submissions on an OFAC match, a 422 on an empty red-flag override reason, no valuation/offer possible on a security-held appraisal — and a companion test that a *clean* case still fully autonomously completes.

Run everything:

```bash
pytest tests/ -q
```

---

## Step 9 — Wire the orchestration layer

Two integration points in `ai-orchestration-svc`, both HTTP clients calling `dms-svc`:

### 9.1 — Tool functions (`src/services/dms_tools.py`)

Thin `httpx` wrappers, one per `dms-svc` endpoint the agent layer needs:

```python
def apply_trade_valuation(tenant_id: str, appraisal_id: str, valuation_usd: float) -> dict:
    with httpx.Client(base_url=get_settings().dms_svc_base_url, timeout=10.0) as client:
        resp = client.post(f"/api/v1/dms/appraisals/{appraisal_id}/valuation-and-offer",
                            json={"valuation_usd": valuation_usd}, headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()
```

### 9.2 — Per-capability agent functions

For a capability that benefits from live reasoning (trade valuation, retail pricing), give it a deterministic fallback *and* a live-model path, and apply the result immediately in the same function — never split "compute" from "apply" into two functions a caller has to remember to chain:

```python
async def evaluate_trade_appraisal(db, tenant_id, appraisal_id, vin, mileage, condition_notes, client=None) -> TradeValuationResult:
    existing = dms_tools.get_appraisal(tenant_id, appraisal_id)
    if existing.get("status") == "security_hold":
        # log and return — this agent has no path to override a security hold
        ...

    client = client or AnthropicAgentClient()
    if client.is_configured:
        # live reasoning path — call submit_valuation via tool use
        ...
    else:
        # deterministic fallback — honest that no comp data exists, or a value with the reasoning marked as such
        ...

    if valuation_usd is not None:
        applied = dms_tools.apply_trade_valuation(tenant_id, appraisal_id, valuation_usd)
        offer_usd = applied.get("offer_usd")
    # ... write the DecisionLog row, return valuation_usd AND offer_usd
```

For capabilities that are purely orchestration (invoke the right `dms-svc` sweep at the right trigger, no reasoning needed since the decision math already lives in `dms-svc`), use a shared thin shell so every wrapper is a one-line application of it:

```python
async def _run(db, tenant_id, trigger_event_type, trigger_context, action_taken, reasoning, call):
    status = await _agent_status(db, tenant_id)
    if status != AgentStatus.ACTIVE:
        log = DecisionLog(..., action_taken="skipped_agent_not_active", outcome="deferred")
        db.add(log)
        await db.commit()
        return DealerOrchestrationResult(decision_log=log, output=None, skipped_reason=status.value)

    output = call()
    log = DecisionLog(tenant_id=tenant_id, agent_key=AGENT_KEY, trigger_event_type=trigger_event_type,
                       trigger_context=trigger_context, reasoning=reasoning, action_taken=action_taken, outcome="success")
    db.add(log)
    await db.commit()
    return DealerOrchestrationResult(decision_log=log, output=output)


async def evaluate_parts_reorder(db, tenant_id) -> DealerOrchestrationResult:
    return await _run(db, tenant_id, "parts.reorder_sweep_requested", {}, "vendor_orders_placed",
                       "[deterministic] Swept parts stock for anything at or below reorder point and placed vendor orders autonomously.",
                       lambda: dms_tools.evaluate_parts_reorder(tenant_id))
```

For the lending wrapper specifically, write the security framing directly into the `DecisionLog` reasoning text, conditionally, so the audit trail itself explains *why* nothing happened on an OFAC match rather than just recording that nothing happened:

```python
reasoning = (
    "[security] OFAC sanctions match — application permanently blocked before any lender routing was attempted. No override exists."
    if ofac_match else
    "[deterministic] Bureau pull cleared OFAC; auto-routed by FICO tier, auto-decisioned every submission, and auto-selected the winner."
)
```

### 9.3 — Register the new router

```python
# ai-orchestration-svc/src/main.py
from src.api import dealer_intelligence
app.include_router(dealer_intelligence.router)
```

### 9.4 — Verify the wiring live, not just with mocks

Unit tests with mocked `dms_tools` functions confirm your call sites use the right function signatures. They do not confirm the HTTP contract between the two services actually matches. Before considering the integration done, run both services for real and call the agent-side function with no mocking at all:

```bash
# terminal 1
cd services/dms-svc && DMS_DATABASE_URL="sqlite+aiosqlite:///./dms.db" uvicorn src.main:app --port 8008

# terminal 2
cd services/ai-orchestration-svc
AI_ORCH_DMS_SVC_BASE_URL="http://127.0.0.1:8008" python3 -c "
from src.services import dms_tools
result = dms_tools.apply_trade_valuation('test-tenant', '<a real appraisal id>', 15000.0)
print(result)
"
```

Then issue a fresh `GET` against `dms-svc` directly and confirm the value actually landed — a successful `apply_trade_valuation` call and a persisted row are two different claims, and only checking the first one is how a URL typo or a field-name mismatch survives an entire test suite undetected.

---

## Step 10 — Deployment checklist

- [ ] `DMS_DATABASE_URL` points at a real Postgres instance in every non-local environment; SQLite is for tests only
- [ ] `AI_ORCH_DMS_SVC_BASE_URL` (on the `ai-orchestration-svc` side) matches `dms-svc`'s actual service address — the env var prefix is `AI_ORCH_`, not `DMS_`, since it's read by the *other* service
- [ ] Migration applies cleanly against a fresh database before any traffic is routed to the new service
- [ ] The three security-relevant endpoints (`pull-bureau`, `red-flag-checks/{id}/override`, `appraisals/find-or-create`) are covered by monitoring/alerting on their block/hold rates — a sudden spike or a sudden drop to zero is worth a human looking at either way
- [ ] Confirm no code path anywhere calls `unfreeze`-equivalent or override logic for the three security fields except the explicit human-facing endpoints — grep for the field names (`ofac_match`, `overridden_by`, `security_hold`) across the whole `ai-orchestration-svc` codebase, not just the DMS-specific files, before shipping
