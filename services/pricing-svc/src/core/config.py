from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PRICING_", extra="ignore")

    service_name: str = "pricing-svc"
    port: int = 8010
    database_url: str = "sqlite+aiosqlite:///./pricing.db"
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_settings() -> Settings:
    return Settings()
