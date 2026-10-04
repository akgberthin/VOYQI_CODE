from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IP_", extra="ignore")

    service_name: str = "ip-asset-svc"
    port: int = 8028
    database_url: str = "sqlite+aiosqlite:///./ip_asset.db"
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_settings() -> Settings:
    return Settings()
