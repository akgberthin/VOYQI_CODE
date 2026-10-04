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

    # DMS-002 — Retail Inventory: a vehicle at or beyond this many days
    # in stock gets an autonomous aging markdown; the discount grows
    # 0.5%/day beyond the threshold, capped at 15%.
    aging_markdown_threshold_days: int = 60
    aging_markdown_pct_per_day: float = 0.005
    aging_markdown_cap_pct: float = 0.15

    # DMS-003 — Sales Desk: minimum acceptable gross over cost. A
    # desked price that misses this floor is auto-countered up to it,
    # never silently approved and never queued for a manager.
    min_gross_floor_pct: float = 0.03

    # DMS-005 — Trade-in Appraisal: wholesale offer as a fraction of
    # the agent's valuation.
    trade_offer_pct_of_valuation: float = 0.93

    # DMS-004/Lending — FICO tiering for autonomous lender routing and
    # decisioning. These are deterministic business-policy thresholds,
    # not security thresholds — OFAC is handled separately and has no
    # threshold at all (any match blocks, full stop).
    lending_approve_fico_floor: int = 620
    lending_counter_fico_floor: int = 580
    lending_prime_fico_floor: int = 700

    # DMS-006 — Service: a job whose estimated duration is at or above
    # this many days auto-triggers a loaner assignment.
    service_loaner_duration_threshold_days: int = 1

    # DMS-008 — Loaner & Courtesy: a loaner out this many days or more
    # auto-flags for billing handoff.
    loaner_extended_days_threshold: int = 5

    # DMS-001 — Rooftop Directory: a dealer license within this many
    # days of expiry auto-files a renewal.
    license_renewal_window_days: int = 60

    # DMS-009 — P&L capstone: a rooftop verdicted "intervene" gets an
    # accelerated aging sweep at this (tighter) threshold instead of
    # the standard one above.
    intervention_aging_threshold_days: int = 30
    intervention_markdown_cap_pct: float = 0.10
    intervention_markdown_pct_per_day: float = 0.004

    # DMS-007 — Parts: reorder quantity is max(reorder_multiplier *
    # reorder_point, reorder_minimum_qty).
    parts_reorder_minimum_qty: int = 20
    parts_reorder_multiplier: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
