from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="USER_", extra="ignore")

    service_name: str = "user-svc"
    port: int = 8025
    database_url: str = "sqlite+aiosqlite:///./user.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    auth_svc_base_url: str = "http://auth-svc:8024"


@lru_cache
def get_settings() -> Settings:
    return Settings()
