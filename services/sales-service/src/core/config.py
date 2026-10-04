from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALES_", extra="ignore")

    service_name: str = "sales-service"
    port: int = 8009
    database_url: str = "sqlite+aiosqlite:///./sales.db"
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_settings() -> Settings:
    return Settings()
