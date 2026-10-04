from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXEC_", extra="ignore")

    service_name: str = "executive-svc"
    # 8025 collides with the client-delivered user-svc, which claims
    # that port in its own default config — moved here to a free slot
    # rather than edit code that isn't ours.
    port: int = 8032
    database_url: str = "sqlite+aiosqlite:///./executive.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # How many days a triggered alert can sit unresolved before it's
    # treated as stale in reporting views — a threshold breach from one
    # bad data point shouldn't stay "open" forever with no new snapshot
    # to confirm or clear it.
    alert_stale_after_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
