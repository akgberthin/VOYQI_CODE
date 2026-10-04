from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBSCRIPTION_", extra="ignore")

    service_name: str = "subscription-svc"
    port: int = 8003
    database_url: str = "sqlite+aiosqlite:///./subscription.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # MOB-006 — how long a swap request can sit unfulfilled before it
    # breaches SLA and gets escalated.
    swap_sla_hours: int = 24

    # MOB-005 — GU fraud/abuse heuristic: more than this many earn
    # events for one member in the window below is flagged.
    gu_fraud_earn_count_threshold: int = 10
    gu_fraud_window_hours: int = 24

    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"
    fleet_svc_base_url: str = "http://fleet-svc:8004"


@lru_cache
def get_settings() -> Settings:
    return Settings()
