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

    request_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
