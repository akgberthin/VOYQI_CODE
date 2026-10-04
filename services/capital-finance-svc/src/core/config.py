from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAPFIN_", extra="ignore")

    service_name: str = "capital-finance-svc"
    # 8028 was PORT_REGISTRY.md's original assignment, but the client-
    # delivered ip-asset-svc claims it — and, more importantly, the
    # client-delivered api-gateway-svc's routing table hardcodes
    # ip-asset-svc at that port. Moved here rather than edit gateway
    # code that isn't ours.
    port: int = 8035
    database_url: str = "sqlite+aiosqlite:///./capital_finance.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    alert_stale_after_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
