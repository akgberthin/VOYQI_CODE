from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_", extra="ignore")

    service_name: str = "auth-svc"
    port: int = 8024
    database_url: str = "sqlite+aiosqlite:///./auth.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # JWT signing. The default is an obviously-insecure dev placeholder —
    # production deployment MUST override AUTH_JWT_SECRET via env var/secret
    # manager. main.py refuses to start with the default value unless
    # AUTH_ALLOW_INSECURE_DEV_SECRET=true is explicitly set.
    jwt_secret: str = "INSECURE-DEV-ONLY-CHANGE-ME"
    jwt_algorithm: str = "HS256"
    allow_insecure_dev_secret: bool = False

    access_token_ttl_minutes: int = 15
    # Shared with api-gateway-svc for early access-token revocation (src/core/revocation.py).
    redis_url: str | None = None
    refresh_token_ttl_days: int = 30

    # Account lockout policy (brute-force protection) — deterministic,
    # not AI-mediated; this is a fixed security control, not a judgment call.
    max_failed_login_attempts: int = 5
    lockout_duration_minutes: int = 15

    # Password reset tokens are single-use and short-lived.
    password_reset_ttl_minutes: int = 30

    cookie_secure: bool = True
    cookie_domain: str | None = None

    notification_svc_base_url: str = "http://notification-svc:8027"

    # Shared secret rbac-svc presents on /internal/ calls (lockout status
    # and unlock for the Super Admin panel). Empty disables those routes.
    internal_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
