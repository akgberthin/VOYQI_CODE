from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYMENT_", extra="ignore")

    service_name: str = "payment-svc"
    port: int = 8026
    database_url: str = "sqlite+aiosqlite:///./payment.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # Real processor credentials — absent in this environment. Without
    # them, the sandbox provider is used: it simulates realistic
    # success/decline behavior and is clearly marked as such on every
    # Charge record (provider_mode), never presented as a real charge.
    stripe_api_key: str | None = None
    stripe_webhook_secret: str | None = None

    notification_svc_base_url: str = "http://notification-svc:8027"


@lru_cache
def get_settings() -> Settings:
    return Settings()
