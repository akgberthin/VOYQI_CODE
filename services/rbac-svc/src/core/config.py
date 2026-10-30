from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RBAC_", extra="ignore")

    service_name: str = "rbac-svc"
    port: int = 8011
    database_url: str = "sqlite+aiosqlite:///./rbac.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"
    ai_initiated_header_value: str = "compliance_audit_agent"

    # SAP-002 — deny-wins: an explicit DENY on any matching permission
    # always overrides an ALLOW from any other role or override, no
    # exceptions. This is enforced in the engine, not just documented.
    deny_wins: bool = True

    # Integrations: Fernet key (urlsafe base64, 32 bytes) that encrypts
    # stored third-party credentials. Without it credentials can't be
    # saved. Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    integrations_key: str = ""
    # Shared secret other services present on /internal/ calls. The
    # gateway doesn't route /internal/, so it's never reachable from
    # outside; this guards against other pods in the namespace too.
    internal_token: str = ""
    # auth-svc answers lockout / last-login questions for the Users page.
    auth_svc_base_url: str = "http://auth-svc:8024"
    # The gateway asks for a user's permissions on their requests; at most
    # this often that also stamps the user's "last seen".
    last_seen_resolution_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
