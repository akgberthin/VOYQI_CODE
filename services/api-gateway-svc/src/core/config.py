from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")

    service_name: str = "api-gateway-svc"
    port: int = 8000

    # Must match auth-svc's AUTH_JWT_SECRET exactly in every real
    # deployment — this is what lets the gateway verify tokens
    # statelessly (no per-request call to auth-svc). Same insecure
    # default + same startup guard as auth-svc.
    jwt_secret: str = "INSECURE-DEV-ONLY-CHANGE-ME"
    jwt_algorithm: str = "HS256"
    allow_insecure_dev_secret: bool = False

    # In-memory sliding-window rate limit. This is honestly a
    # single-replica mechanism — a real multi-replica deployment needs
    # a shared store (Redis) for the counters to mean anything across
    # instances. Documented here rather than silently wrong at scale.
    rate_limit_requests_per_window: int = 100
    rate_limit_window_seconds: int = 60

    # Shared Redis (Memorystore in production): rate-limit counters across
    # replicas, and the access-token revocation list auth-svc writes. When
    # unset, rate limiting is per-process and revocation isn't checked.
    redis_url: str | None = None

    request_timeout_seconds: float = 30.0

    # Role-based authorization (src/services/authorization.py). On by
    # default; permissions are fetched from rbac-svc and cached briefly
    # per user, so a role change takes effect within this many seconds.
    enforce_rbac: bool = True
    rbac_base_url: str = "http://rbac-svc:8011"
    permission_cache_seconds: float = 30.0

    # CORS (src/services/cors.py): fixed origins here, plus the ones super
    # admin lists under Platform settings → Security (read from rbac-svc
    # with the shared internal token).
    cors_allowed_origins: str = ""
    cors_refresh_seconds: float = 60.0
    internal_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
