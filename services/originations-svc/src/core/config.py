from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORIG_", extra="ignore")

    service_name: str = "originations-svc"
    # docs/PORT_REGISTRY.md's original 8024 collides with the client-
    # delivered auth-svc, which claims that port. Moved here rather
    # than edit code that isn't ours.
    port: int = 8036
    database_url: str = "sqlite+aiosqlite:///./originations.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # Deterministic lead-scoring bands — named, not buried in a
    # function body. Score is 0-100: higher income-to-request ratio
    # and higher FICO both push it up.
    hot_score_threshold: float = 70.0
    warm_score_threshold: float = 40.0

    # ORG-002: the fixed bar the Revenue Agent's 0.0-1.0 risk score is
    # held to (higher = lower risk). The agent scores; this threshold —
    # not the agent — produces the recommended outcome, so every case is
    # held to the same bar. The final approve/decline is still a tier-3
    # human decision.
    org_approve_risk_score: float = 0.7
    org_decline_risk_score: float = 0.4


@lru_cache
def get_settings() -> Settings:
    return Settings()
