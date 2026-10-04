from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./hrm.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_initiated_header_value: str = "true"

    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"

    # HRM-005 — attrition risk. Mirrors the Member Experience Agent's
    # churn-scoring discipline: a deterministic, fully auditable
    # heuristic is the floor, never a black box.
    attrition_risk_review_threshold: float = 0.65

    model_config = SettingsConfigDict(env_prefix="HRM_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
