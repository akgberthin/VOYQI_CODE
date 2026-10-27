from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTIF_", extra="ignore")

    service_name: str = "notification-svc"
    port: int = 8027
    database_url: str = "sqlite+aiosqlite:///./notification.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # Email: real SMTP delivery if configured, otherwise the console
    # provider logs the rendered content and records delivery_mode as
    # "console" rather than silently pretending to send.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str = "no-reply@voyqi.com"
    smtp_use_tls: bool = True

    # SMS: same honesty principle — no real provider credentials means
    # console mode, not a fabricated "sent" status.
    sms_provider_api_key: str | None = None
    sms_from_number: str | None = None

    push_provider_api_key: str | None = None

    # Per-tenant provider credentials set in the super admin console live
    # in rbac-svc; they take precedence over the env settings above.
    rbac_base_url: str = "http://rbac-svc:8011"
    internal_token: str = ""
    integrations_cache_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
