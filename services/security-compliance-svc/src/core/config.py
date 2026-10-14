from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECCOMP_", extra="ignore")

    service_name: str = "security-compliance-svc"
    port: int = 8031
    database_url: str = "sqlite+aiosqlite:///./security_compliance.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # Privileged-access sessions (break-glass, impersonation): what counts
    # as "outside business hours" for the Compliance/Audit Agent's risk
    # scoring. Evaluated in the operator's own timezone, not UTC.
    business_timezone: str = "America/New_York"
    business_hours_start: int = 8
    business_hours_end: int = 19
    break_glass_max_minutes: int = 240


@lru_cache
def get_settings() -> Settings:
    return Settings()
