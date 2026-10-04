from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRM_", extra="ignore")

    service_name: str = "crm-svc"
    port: int = 8021
    database_url: str = "sqlite+aiosqlite:///./crm.db"
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_settings() -> Settings:
    return Settings()
