from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./hub.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_initiated_header_value: str = "true"

    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"

    # HUB-002 — geographic anomaly detection on VOYQI Card taps. A speed
    # implied between two consecutive taps above this threshold is
    # physically implausible (faster than any real combination of
    # flight + ground transport for a short gap) and is treated as a
    # hard security signal: the card is auto-suspended and the event is
    # always reviewed by the Compliance/Audit Agent.
    card_anomaly_max_plausible_kmh: float = 900.0

    model_config = SettingsConfigDict(env_prefix="HUB_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
