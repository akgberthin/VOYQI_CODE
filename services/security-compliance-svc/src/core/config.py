from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECCOMP_", extra="ignore")

    service_name: str = "security-compliance-svc"
    port: int = 8031
    database_url: str = "sqlite+aiosqlite:///./security_compliance.db"
    kafka_bootstrap_servers: str = "kafka:9092"


@lru_cache
def get_settings() -> Settings:
    return Settings()
