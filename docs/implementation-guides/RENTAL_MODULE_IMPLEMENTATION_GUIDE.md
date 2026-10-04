# Rental Management Module
## Implementation Guide

A step-by-step guide to implementing `rental-svc` — fleet, reservations, real-time utilization, demand forecasting, dynamic pricing, and predictive insights for both the renter and the hub administrator — plus the agent layer that makes pricing decisions autonomously and the admin console that operates the whole system.

**Governing principle:** every capability executes autonomously by default. Vehicle allocation, pricing, forecasting, and insight generation all run with no approval gate — none of it is an access-control decision. The one structural rule enforced rather than left to convention: a reservation's rate can never be modified once it reaches `ACTIVE` or `COMPLETED`. This raises rather than silently no-ops, so a caller can't mistake a refused change for a successful one.

**The pricing model is deliberately two-factor, not one.** Utilization tells you how full a hub is right now — a pure supply signal. It cannot tell you whether that hub has been flat at 70% for a week or hit 70% in the last six hours. Booking velocity and trend are a genuinely separate demand signal, computed from a real time series, not derived from utilization. The two are combined, not layered — a hub at neutral utilization with accelerating demand still gets a real price premium, and a hub at high utilization with decelerating demand gets a smaller one than utilization alone would suggest.

---

## Step 1 — Scaffold the service

```
services/rental-svc/
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

The core infrastructure files — `database.py` (async SQLAlchemy engine, cross-dialect `GUID` type), `security.py` (`get_tenant_id`, hard-requiring the `X-Tenant-Id` header), `kafka_events.py` (fire-and-forget publisher) — follow the same pattern used across every service on this platform.

`src/core/config.py` names every tunable explicitly — a pricing/forecasting engine with magic numbers buried in function bodies is unauditable:

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RENTAL_", extra="ignore")

    service_name: str = "rental-svc"
    port: int = 8005
    database_url: str = "sqlite+aiosqlite:///./rental.db"

    # Supply-side (utilization): matches a previously-documented
    # 80% -> 1.0x, 100% -> 1.25x scaling up; 40% -> 1.0x, 0% -> 0.85x
    # scaling down. The broad middle is deliberately left alone.
    supply_high_util_threshold: float = 80.0
    supply_high_util_max_multiplier: float = 1.25
    supply_low_util_threshold: float = 40.0
    supply_low_util_min_multiplier: float = 0.85

    # Demand-side (booking velocity) — the genuinely separate signal.
    demand_velocity_window_days: int = 7
    demand_trend_comparison_days: int = 3
    demand_accelerating_multiplier: float = 1.10
    demand_decelerating_multiplier: float = 0.95
    demand_trend_threshold_pct: float = 20.0
    # A zero prior-window baseline is treated as STEADY, not
    # automatically ACCELERATING, unless recent velocity clears this
    # floor — otherwise a single isolated booking after a quiet
    # period reads as a demand spike on pure noise.
    demand_min_velocity_for_zero_baseline_trend: float = 1.0

    # Combined multiplier bounds — a hard ceiling/floor regardless of
    # how extreme either signal gets, so pricing never runs away in
    # either direction on a single event.
    combined_multiplier_min: float = 0.75
    combined_multiplier_max: float = 1.50

    # Predictive forecasting and insight thresholds
    forecast_horizon_days: int = 3
    capacity_risk_threshold_pct: float = 90.0
    underutilized_threshold_pct: float = 25.0
    low_availability_client_threshold_pct: float = 85.0

    # Fleet reallocation
    reallocation_min_utilization_gap_pct: float = 40.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## Step 2 — Enable CORS

Any admin console or renter-facing frontend calls this service directly from the browser:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Narrows to the gateway's origin only in production, per the platform's gateway-only-ingress principle.

---

## Step 3 — Data model

Eight tables, one file. Separate the raw demand signal from the reservations that produce it — a cancelled reservation still happened as a demand event even after the reservation record itself is gone.

```python
class RentalClass(str, enum.Enum):
    URBAN = "urban"; FAMILY = "family"; EXECUTIVE = "executive"
    ADVENTURE = "adventure"; LUXURY = "luxury"; PERFORMANCE = "performance"


class RentalVehicle(Base):
    __tablename__ = "rental_vehicles"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    vin: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    base_daily_rate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(Enum(VehicleStatus), nullable=False, default=VehicleStatus.AVAILABLE)


class Reservation(Base):
    """vehicle_id is null until autonomous allocation assigns one at
    confirmation. Once status reaches ACTIVE or COMPLETED,
    daily_rate_usd is structurally frozen — see Step 4."""
    __tablename__ = "reservations"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(GUID, ForeignKey("rental_vehicles.id"), nullable=True)
    daily_rate_usd: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(Enum(ReservationStatus), nullable=False, default=ReservationStatus.PENDING)


class BookingEvent(Base):
    """The raw time series booking_velocity is computed from — not
    the same table as Reservation, since a cancelled booking is still
    a real demand signal that happened."""
    __tablename__ = "booking_events"
    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hub_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rental_class: Mapped[RentalClass] = mapped_column(Enum(RentalClass), nullable=False)
    event_type: Mapped[BookingEventType] = mapped_column(Enum(BookingEventType), nullable=False)  # booked | cancelled
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UtilizationSnapshot(Base):
    """The supply-side snapshot."""
    __tablename__ = "utilization_snapshots"
    # tenant_id, hub_id, rental_class, booked_count, total_count, utilization_pct, snapshot_at


class DemandForecast(Base):
    """Predictive analytics, autonomously generated."""
    __tablename__ = "demand_forecasts"
    # tenant_id, hub_id, rental_class, booking_velocity_per_day, trend, forecasted_utilization_pct, reasoning, generated_at


class PricingAdjustmentLog(Base):
    """Every dynamic rate change, with the full supply+demand
    breakdown attached — not just the resulting number."""
    __tablename__ = "pricing_adjustment_logs"
    # tenant_id, reservation_id, old_rate_usd, new_rate_usd, utilization_pct,
    # supply_multiplier, demand_multiplier, combined_multiplier, reasoning, adjusted_by, adjusted_at


class ClientInsight(Base):
    """Instructive to the renter."""
    __tablename__ = "client_insights"
    # tenant_id, hub_id, rental_class, insight_type, message, generated_at


class AdminInsight(Base):
    """Instructive to the hub administrator — a predictive, actionable
    flag rather than a raw metric they'd have to interpret themselves."""
    __tablename__ = "admin_insights"
    # tenant_id, hub_id, rental_class, insight_type, severity, message, resolved, generated_at
```

---

## Step 4 — Build the engine layer

### 4.1 — Fleet and reservations, with the structural rate-freeze rule

```python
class ReservationLockedError(ValueError): pass
class NoVehicleAvailableError(ValueError): pass


async def create_reservation(db, tenant_id, hub_id, rental_class, customer_name, start_date, end_date, daily_rate_usd) -> Reservation:
    reservation = Reservation(tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, customer_name=customer_name,
                               start_date=start_date, end_date=end_date, daily_rate_usd=daily_rate_usd)
    db.add(reservation)
    # The real demand signal is recorded the instant someone books,
    # not when a vehicle is later allocated — that's what booking
    # velocity is actually measuring.
    db.add(BookingEvent(tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class, event_type=BookingEventType.BOOKED))
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def confirm_reservation(db, tenant_id, reservation_id) -> Reservation:
    """Fully autonomous allocation — the first available vehicle
    matching hub+class is assigned in this same call, no human picks
    which physical unit a renter gets."""
    reservation = await get_reservation(db, tenant_id, reservation_id)
    result = await db.execute(select(RentalVehicle).where(
        RentalVehicle.tenant_id == tenant_id, RentalVehicle.hub_id == reservation.hub_id,
        RentalVehicle.rental_class == reservation.rental_class, RentalVehicle.status == VehicleStatus.AVAILABLE,
    ))
    vehicle = result.scalars().first()
    if vehicle is None:
        raise NoVehicleAvailableError(f"no available {reservation.rental_class.value} vehicle at hub {reservation.hub_id}")
    reservation.vehicle_id = vehicle.id
    reservation.status = ReservationStatus.CONFIRMED
    await db.commit()
    await db.refresh(reservation)
    return reservation


async def modify_reservation(db, tenant_id, reservation_id, new_daily_rate_usd) -> Reservation:
    """The one structural rule in this engine: refused outright —
    raised, not silently ignored — once the reservation has reached
    ACTIVE or COMPLETED."""
    reservation = await get_reservation(db, tenant_id, reservation_id)
    if reservation.status in (ReservationStatus.ACTIVE, ReservationStatus.COMPLETED):
        raise ReservationLockedError(f"reservation is {reservation.status.value} — the rate can only be modified before checkout")
    reservation.daily_rate_usd = new_daily_rate_usd
    await db.commit()
    await db.refresh(reservation)
    return reservation
```

### 4.2 — Demand: velocity, trend, and the noise floor that prevents overreacting to a single booking

```python
async def compute_booking_velocity(db, tenant_id, hub_id, rental_class, window_days, end_at=None) -> float:
    """Net bookings per day — a cancellation reduces velocity the
    same way a booking increases it, since both are real demand signals."""
    end_at = end_at or datetime.now(timezone.utc)
    start_at = end_at - timedelta(days=window_days)
    events = await _events_in_window(db, tenant_id, hub_id, rental_class, start_at, end_at)
    net = sum(1 if e.event_type == BookingEventType.BOOKED else -1 for e in events)
    return round(net / window_days, 2) if window_days else 0.0


async def classify_trend(db, tenant_id, hub_id, rental_class) -> DemandTrend:
    """Compares the most recent comparison window against the window
    immediately before it — a real before/after comparison, not a
    single-window snapshot mistaken for a trend.

    A zero prior-window baseline is treated as STEADY unless recent
    velocity clears a minimum floor — without that floor, a single
    isolated booking after a quiet period reads as a demand spike on
    pure noise, not signal."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    recent = await compute_booking_velocity(db, tenant_id, hub_id, rental_class, settings.demand_trend_comparison_days, end_at=now)
    prior_end = now - timedelta(days=settings.demand_trend_comparison_days)
    prior = await compute_booking_velocity(db, tenant_id, hub_id, rental_class, settings.demand_trend_comparison_days, end_at=prior_end)

    if prior == 0:
        return DemandTrend.ACCELERATING if recent >= settings.demand_min_velocity_for_zero_baseline_trend else DemandTrend.STEADY

    pct_change = ((recent - prior) / abs(prior)) * 100
    if pct_change >= settings.demand_trend_threshold_pct:
        return DemandTrend.ACCELERATING
    if pct_change <= -settings.demand_trend_threshold_pct:
        return DemandTrend.DECELERATING
    return DemandTrend.STEADY


async def generate_forecast(db, tenant_id, hub_id, rental_class) -> DemandForecast:
    """A real deterministic projection — current occupancy plus the
    recent net booking rate, projected across the horizon and capped
    at capacity — not a guess."""
    settings = get_settings()
    current = await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)
    velocity = await compute_booking_velocity(db, tenant_id, hub_id, rental_class, settings.demand_velocity_window_days)
    trend = await classify_trend(db, tenant_id, hub_id, rental_class)

    total = current["total_count"]
    projected_booked = max(0, min(total, current["booked_count"] + velocity * settings.forecast_horizon_days))
    forecasted_pct = round((projected_booked / total) * 100, 1) if total else 0.0

    forecast = DemandForecast(tenant_id=tenant_id, hub_id=hub_id, rental_class=rental_class,
                               booking_velocity_per_day=velocity, trend=trend, forecasted_utilization_pct=forecasted_pct,
                               reasoning=f"[deterministic] util {current['utilization_pct']}% · velocity {velocity:+.2f}/day · trend {trend.value} · projected {forecasted_pct}% in {settings.forecast_horizon_days}d.")
    db.add(forecast)
    await db.commit()
    await db.refresh(forecast)
    return forecast
```

Write the trend-classification test in both directions, not just one: a single isolated booking must classify as `STEADY`, and a genuine run of bookings in the same window must still classify as `ACCELERATING`. A fix that suppresses noise can just as easily suppress real signal if only tested one way.

### 4.3 — Pricing: the two-factor model, kept as pure functions

```python
def compute_supply_multiplier(utilization_pct: float) -> float:
    """Piecewise, explainable: high utilization scales the rate up,
    low utilization scales it down, the broad middle is left alone."""
    settings = get_settings()
    if utilization_pct >= settings.supply_high_util_threshold:
        span = 100 - settings.supply_high_util_threshold
        return 1.0 + (utilization_pct - settings.supply_high_util_threshold) / span * (settings.supply_high_util_max_multiplier - 1.0)
    if utilization_pct <= settings.supply_low_util_threshold:
        span = settings.supply_low_util_threshold
        return 1.0 - (settings.supply_low_util_threshold - utilization_pct) / span * (1.0 - settings.supply_low_util_min_multiplier)
    return 1.0


def compute_demand_multiplier(trend: DemandTrend) -> float:
    """The genuinely separate signal: accelerating demand justifies a
    premium on top of whatever utilization alone would suggest;
    decelerating demand justifies easing off, even at unchanged utilization."""
    settings = get_settings()
    if trend == DemandTrend.ACCELERATING:
        return settings.demand_accelerating_multiplier
    if trend == DemandTrend.DECELERATING:
        return settings.demand_decelerating_multiplier
    return 1.0


async def recommend_rate(db, tenant_id, reservation_id) -> RateRecommendation:
    """Read-only — safe to call as often as needed (e.g. a live
    renter-facing quote) with no side effects."""
    settings = get_settings()
    reservation = await reservation_engine.get_reservation(db, tenant_id, reservation_id)
    utilization = await utilization_engine.compute_utilization(db, tenant_id, reservation.hub_id, reservation.rental_class.value)
    forecast = await demand_engine.generate_forecast(db, tenant_id, reservation.hub_id, reservation.rental_class.value)

    supply_mult = compute_supply_multiplier(utilization["utilization_pct"])
    demand_mult = compute_demand_multiplier(forecast.trend)
    combined = max(settings.combined_multiplier_min, min(settings.combined_multiplier_max, supply_mult * demand_mult))
    recommended_rate = round(reservation.daily_rate_usd * combined, 2)

    return RateRecommendation(reservation_id=reservation_id, current_rate_usd=reservation.daily_rate_usd,
                               recommended_rate_usd=recommended_rate, utilization_pct=utilization["utilization_pct"],
                               booking_velocity_per_day=forecast.booking_velocity_per_day, trend=forecast.trend.value,
                               supply_multiplier=round(supply_mult, 4), demand_multiplier=round(demand_mult, 4),
                               combined_multiplier=round(combined, 4), reasoning="...")


async def apply_recommendation(db, tenant_id, reservation_id, adjusted_by, min_change_pct=1.0):
    """Fully autonomous: computes the recommendation and, if it
    differs meaningfully, applies it and logs the full breakdown in
    this same call. A negligible change is reported but not applied —
    not every booking needs a price change."""
    recommendation = await recommend_rate(db, tenant_id, reservation_id)
    pct_change = abs(recommendation.recommended_rate_usd - recommendation.current_rate_usd) / recommendation.current_rate_usd * 100 if recommendation.current_rate_usd else 0
    if pct_change < min_change_pct:
        return recommendation, None

    await reservation_engine.modify_reservation(db, tenant_id, reservation_id, recommendation.recommended_rate_usd)
    log = PricingAdjustmentLog(tenant_id=tenant_id, reservation_id=reservation_id, old_rate_usd=recommendation.current_rate_usd,
                                new_rate_usd=recommendation.recommended_rate_usd, utilization_pct=recommendation.utilization_pct,
                                supply_multiplier=recommendation.supply_multiplier, demand_multiplier=recommendation.demand_multiplier,
                                combined_multiplier=recommendation.combined_multiplier, reasoning=recommendation.reasoning, adjusted_by=adjusted_by)
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return recommendation, log
```

Write a test that proves the divergence directly: identical utilization, different trends, must produce different combined multipliers, and the accelerating case must exceed the decelerating case. This is the one property the whole two-factor design exists to deliver — test it as its own assertion, not as an implied side effect of a broader scenario test.

### 4.4 — Insights and reallocation: the same data, two audiences and one cross-hub view

```python
async def generate_client_insights(db, tenant_id, hub_id, rental_class) -> list[ClientInsight]:
    """The same forecast the pricing engine reads, translated into a
    plain-language nudge rather than a raw percentage."""
    settings = get_settings()
    forecast = await demand_engine.generate_forecast(db, tenant_id, hub_id, rental_class)
    insights = []
    if forecast.forecasted_utilization_pct >= settings.low_availability_client_threshold_pct:
        insights.append(ClientInsight(..., insight_type=ClientInsightType.LOW_AVAILABILITY, message=f"Availability is tight — projected {forecast.forecasted_utilization_pct}% booked soon."))
    if forecast.trend == DemandTrend.ACCELERATING:
        insights.append(ClientInsight(..., insight_type=ClientInsightType.PRICE_RISING, message="Demand is picking up — booking now typically locks in a lower rate."))
    # ... persist and return


async def generate_admin_insights(db, tenant_id, hub_id, rental_class) -> list[AdminInsight]:
    """A predictive, actionable flag rather than a metric the admin
    has to interpret and act on themselves."""
    settings = get_settings()
    utilization = await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)
    forecast = await demand_engine.generate_forecast(db, tenant_id, hub_id, rental_class)
    insights = []
    if forecast.forecasted_utilization_pct >= settings.capacity_risk_threshold_pct:
        insights.append(AdminInsight(..., insight_type=AdminInsightType.CAPACITY_RISK, severity=..., message="Consider adding capacity or reallocating from an underutilized hub."))
    elif utilization["utilization_pct"] <= settings.underutilized_threshold_pct:
        insights.append(AdminInsight(..., insight_type=AdminInsightType.UNDERUTILIZED, message="A reallocation candidate if another hub of the same class is approaching capacity."))
    # ... persist and return


async def find_reallocation_opportunities(db, tenant_id, rental_class) -> list[ReallocationSuggestion]:
    """Autonomous cross-hub scan: every pair of hubs carrying this
    class is compared, surfacing a specific, actionable suggestion —
    not a generic 'rebalance fleet' alert. Proposing the move is
    autonomous; executing a physical transfer is a real logistics
    operation with a cost this service has no visibility into, left
    to the administrator to actually carry out."""
    hub_ids = await _distinct_hubs_for_class(db, tenant_id, rental_class)
    utilizations = {h: await utilization_engine.compute_utilization(db, tenant_id, h, rental_class) for h in hub_ids}
    suggestions = []
    for donor_id, donor in utilizations.items():
        for recipient_id, recipient in utilizations.items():
            if donor_id == recipient_id:
                continue
            gap = recipient["utilization_pct"] - donor["utilization_pct"]
            if gap >= get_settings().reallocation_min_utilization_gap_pct and donor["available_count"] > 0:
                suggestions.append(ReallocationSuggestion(donor_hub_id=donor_id, recipient_hub_id=recipient_id, utilization_gap_pct=round(gap, 1), reasoning=f"..."))
    return suggestions
```

---

## Step 5 — Build the API layer

One router per capability area. The two endpoints an external pricing agent depends on are the exact-contract surface — get the paths and field names right the first time, since a mismatched path fails silently as a 404 an agent's own error handling can swallow into "no data available" rather than a loud failure:

```python
@router.get("/hubs/{hub_id}/utilization")
async def get_utilization(hub_id: str, rental_class: str, tenant_id=Depends(get_tenant_id), db=Depends(get_db)):
    return await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)


@router.get("/reservations/{reservation_id}")
async def get_reservation(reservation_id: uuid.UUID, tenant_id=Depends(get_tenant_id), db=Depends(get_db)):
    reservation = await reservation_engine.get_reservation(db, tenant_id, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation


@router.post("/reservations/{reservation_id}/modify")
async def modify_reservation(reservation_id: uuid.UUID, body: ModifyRequest, tenant_id=Depends(get_tenant_id), db=Depends(get_db)):
    """Refuses (409) once the reservation has reached ACTIVE or COMPLETED."""
    try:
        return await reservation_engine.modify_reservation(db, tenant_id, reservation_id, body.daily_rate_usd)
    except reservation_engine.ReservationLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
```

Expose the full recommendation as its own read-only endpoint, separate from applying it — this is what keeps the actual multiplier math in exactly one place:

```python
@router.get("/pricing/reservations/{reservation_id}/recommendation")
async def recommend_rate(reservation_id: uuid.UUID, tenant_id=Depends(get_tenant_id), db=Depends(get_db)):
    """Read-only — safe to call for a live quote as often as needed."""
    return await pricing_engine.recommend_rate(db, tenant_id, reservation_id)


@router.post("/pricing/reservations/{reservation_id}/apply-recommendation")
async def apply_recommendation(reservation_id: uuid.UUID, body: ApplyRequest, tenant_id=Depends(get_tenant_id), db=Depends(get_db)):
    recommendation, log = await pricing_engine.apply_recommendation(db, tenant_id, reservation_id, body.adjusted_by, body.min_change_pct)
    return {"recommendation": recommendation, "applied": log is not None, "log_id": log.id if log else None}
```

---

## Step 6 — Assemble the application

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api import analytics, fleet, hubs, insights, pricing, reallocation, reservations
from src.core.database import init_models

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield

app = FastAPI(title="rental-svc", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for router_module in (fleet, reservations, hubs, pricing, insights, reallocation, analytics):
    app.include_router(router_module.router)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "rental-svc"}
```

---

## Step 7 — Generate and verify the migration

```bash
alembic revision --autogenerate -m "initial rental-svc schema"
```

Check the generated file for a missing `import src.core.database` — autogenerate sometimes emits a `GUID()` column type reference without it. Then verify against a fresh database, not just that the command exits cleanly:

```bash
alembic upgrade head
python3 -c "
import sqlite3
conn = sqlite3.connect('rental.db')
tables = sorted(r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\"))
print(len(tables), tables)
"
```

---

## Step 8 — Write the test suite

The tests worth writing check a *guarantee*, not just a happy path:

- **Reservations**: `confirm` autonomously allocates a vehicle; confirming with no available vehicle is refused (409); the rate-freeze rule holds in both directions (`ACTIVE` and `COMPLETED` each independently refuse a modify); cancelling frees the vehicle and records a real demand event.
- **Pricing (pure functions)**: supply multiplier is neutral in the middle band and scales correctly at both extremes; demand multiplier is neutral when steady; the divergence test — identical utilization, opposite trends, must produce different (and correctly ordered) combined multipliers.
- **Pricing (integration)**: `recommend_rate` never writes anything, verified by re-fetching the reservation afterward; `apply_recommendation` applies and logs when the change is meaningful, and does neither when it isn't.
- **Demand**: a single isolated booking classifies as `STEADY`; a genuine run of bookings still classifies as `ACCELERATING` — test both directions of the noise-floor fix, since a fix that suppresses noise can suppress real signal too if only checked one way.
- **Insights & reallocation**: the right insight type fires at the right threshold in both directions (capacity risk vs. underutilized); a genuinely lopsided pair of hubs produces a suggestion naming the correct donor and recipient; a balanced pair produces none.

```bash
pytest tests/ -q
```

---

## Step 9 — Agent-layer integration: keep the math in exactly one place

A pricing agent that reasons about rentals should never reimplement the supply/demand multiplier formulas itself — that duplication is exactly how two copies of the same logic quietly drift apart. Expose the full recommendation as a tool the agent calls, and let the agent's own job shrink to: fetch the recommendation, decide whether to act, log the reasoning, execute.

```python
def get_pricing_recommendation(tenant_id: str, reservation_id: str) -> dict:
    """The real two-factor computation lives in rental-svc's own
    pricing_engine — this just fetches the result."""
    with httpx.Client(base_url=get_settings().rental_svc_base_url, timeout=10.0) as client:
        resp = client.get(f"/api/v1/rental/pricing/reservations/{reservation_id}/recommendation", headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()
```

The agent's deterministic fallback becomes a thin decision layer, not a second pricing model:

```python
async def evaluate_rental_pricing(db, tenant_id, reservation_id, hub_id, rental_class, client=None):
    # ... agent-status and live-Claude-path handling omitted for brevity ...
    recommendation = rental_tools.get_pricing_recommendation(tenant_id, reservation_id)
    recommended_rate = recommendation["recommended_rate_usd"]
    current_rate = recommendation["current_rate_usd"]
    pct_change = abs(recommended_rate - current_rate) / current_rate * 100 if current_rate else 0

    if pct_change >= 1.0:
        new_rate = rental_tools.apply_dynamic_rate(tenant_id, reservation_id, recommended_rate)
        reasoning = f"[deterministic fallback] {recommendation['reasoning']}"
    else:
        new_rate = None
        reasoning = f"[deterministic fallback] Combined supply+demand signal doesn't justify a change. {recommendation['reasoning']}"
    # ... write DecisionLog, increment agent.decisions_made, return result ...
```

Give a live Claude tool-use path the same recommendation tool alongside the raw `get_utilization`/`get_demand_forecast`/`get_reservation` tools, so it can inspect either signal independently if it chooses to, but never has to recompute the combined formula by hand.

### Verify against a real running server, not just mocks

A test that monkeypatches `get_pricing_recommendation` proves the agent's decision logic is correct. It does not prove the two services' contracts actually match — a URL typo or a field-name mismatch passes every mocked test and fails silently in production. Before considering the integration done, run the real service and call the real tool functions with no mocking at all:

```python
# terminal 1: uvicorn src.main:app --port 8005 against a real rental-svc

# terminal 2, with RENTAL_SVC_BASE_URL pointed at that real server:
utilization = rental_tools.get_utilization(tenant_id, hub_id, rental_class)
forecast = rental_tools.get_demand_forecast(tenant_id, hub_id, rental_class)
recommendation = rental_tools.get_pricing_recommendation(tenant_id, reservation_id)
updated = rental_tools.apply_dynamic_rate(tenant_id, reservation_id, recommendation["recommended_rate_usd"])

# Then the actual agent function, still with zero mocking:
result = await pricing_intelligence_agent.evaluate_rental_pricing(db, tenant_id, reservation_id, hub_id, rental_class)
```

Confirm the two-factor property survives contact with the real server: a hub sitting at exactly the neutral utilization threshold (supply multiplier = 1.0) with a real accelerating booking pattern must still produce a real price increase driven entirely by the demand signal. If utilization alone would have held the rate and the combined recommendation does too, the demand signal isn't actually reaching the pricing decision — go back and check the wiring, not just the unit tests.

---

## Step 10 — Deployment checklist

- [ ] `RENTAL_DATABASE_URL` points at a real Postgres instance in every non-local environment
- [ ] CORS `allow_origins` narrows from `["*"]` to the gateway's actual origin before production
- [ ] The pricing agent's `RENTAL_SVC_BASE_URL` (or equivalent) points at the real deployed address, not `localhost`, and CORS on `rental-svc` allows that origin
- [ ] Every rental class, insight type, and trend value referenced anywhere in a frontend or agent has a matching entry in the backend's enum — grep for every string literal passed to a `rental_class`/`insight_type` field and cross-check against the model file directly
- [ ] Confirm no code path anywhere reimplements `compute_supply_multiplier` or `compute_demand_multiplier` independently — grep for the function names across the whole codebase; a second copy is the first sign the two-factor model has started to drift
- [ ] The forecast horizon, trend-comparison window, and noise floor are tuned against real booking volume per hub before relying on the forecast for capacity decisions — thresholds calibrated on a high-volume hub can misfire on a low-volume one
- [ ] Migration applies cleanly against a fresh database before any traffic is routed to the new schema
