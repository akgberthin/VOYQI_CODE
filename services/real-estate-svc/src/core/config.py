from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REALESTATE_", extra="ignore")

    service_name: str = "real-estate-svc"
    # docs/PORT_REGISTRY.md assigns 8027, but the client-delivered
    # notification-svc already claims that port — moved here to a free
    # slot rather than edit code that isn't ours.
    port: int = 8034
    database_url: str = "sqlite+aiosqlite:///./real_estate.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    alert_stale_after_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
