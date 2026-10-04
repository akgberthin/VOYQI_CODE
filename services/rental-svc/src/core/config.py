from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RENTAL_", extra="ignore")

    service_name: str = "rental-svc"
    port: int = 8005
    database_url: str = "sqlite+aiosqlite:///./rental.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"

    # ── Supply-side pricing (utilization) ──
    # Matches the platform's own documented behavior: 80% util -> 1.0x,
    # 100% -> 1.25x scaling up; 40% util -> 1.0x, 0% -> 0.85x scaling
    # down. The broad middle is deliberately left alone — not every
    # booking needs a price change.
    supply_high_util_threshold: float = 80.0
    supply_high_util_max_multiplier: float = 1.25
    supply_low_util_threshold: float = 40.0
    supply_low_util_min_multiplier: float = 0.85

    # ── Demand-side pricing (booking velocity) — the genuinely new
    # signal: utilization alone is a snapshot of how full a hub is
    # right now, not how fast it's filling. Two hubs at 70% utilization
    # can be in completely different positions: one flat for a week,
    # one that hit 70% in the last six hours. Velocity and trend
    # capture that difference; utilization alone cannot. ──
    demand_velocity_window_days: int = 7  # rolling window for computing bookings/day
    demand_trend_comparison_days: int = 3  # recent-vs-prior window used to classify trend
    demand_accelerating_multiplier: float = 1.10  # applied on top of the supply multiplier
    demand_decelerating_multiplier: float = 0.95
    demand_trend_threshold_pct: float = 20.0  # % change in velocity to call it accelerating/decelerating
    demand_min_velocity_for_zero_baseline_trend: float = 1.0  # minimum bookings/day before a zero prior-window baseline is called ACCELERATING rather than STEADY — avoids overreacting to a single isolated booking as a demand spike

    # ── Combined multiplier bounds — a hard ceiling/floor regardless
    # of how extreme the supply and demand signals get, so pricing
    # never runs away in either direction on a single event. ──
    combined_multiplier_min: float = 0.75
    combined_multiplier_max: float = 1.50

    # ── Predictive forecasting ──
    forecast_horizon_days: int = 3  # how far ahead generate_forecast projects utilization
    capacity_risk_threshold_pct: float = 90.0  # forecasted utilization above this triggers an admin capacity-risk insight
    underutilized_threshold_pct: float = 25.0  # below this triggers an admin reallocation-opportunity insight
    low_availability_client_threshold_pct: float = 85.0  # above this triggers a client "book now" insight

    # ── Fleet reallocation ──
    reallocation_min_utilization_gap_pct: float = 40.0  # minimum utilization gap between two hubs of the same class before an autonomous reallocation is proposed


@lru_cache
def get_settings() -> Settings:
    return Settings()
