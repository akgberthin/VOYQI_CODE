from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MGMT_", extra="ignore")

    service_name: str = "management-svc"
    # docs/PORT_REGISTRY.md assigns 8026, but the client-delivered
    # payment-svc already claims that port in its own default config —
    # moved here to a free slot rather than edit code that isn't ours.
    port: int = 8033
    database_url: str = "sqlite+aiosqlite:///./management.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # Same staleness window as executive-svc's alert_stale_after_days —
    # kept consistent across the Portfolio Intelligence group.
    alert_stale_after_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
