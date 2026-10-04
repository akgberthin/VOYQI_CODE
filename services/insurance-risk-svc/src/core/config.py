from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", extra="ignore")

    service_name: str = "insurance-risk-svc"
    port: int = 8029
    database_url: str = "sqlite+aiosqlite:///./insurance_risk.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # Deterministic risk-score bands — named here, not buried in a
    # function body, matching the platform-wide rule that a scoring
    # engine's thresholds are always an auditable config value.
    high_risk_score_threshold: float = 75.0
    elevated_risk_score_threshold: float = 50.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
