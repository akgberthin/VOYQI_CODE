"""
Environment-driven configuration for ai-orchestration-svc.

Nothing here is hardcoded that should be an operational knob: the model
name, the pricing-svc base URL, and the Anthropic API key are all
overridable so this service behaves identically in dev (mocked), staging,
and production (live credentials) without a code change.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_ORCH_", extra="ignore")

    service_name: str = "ai-orchestration-svc"
    port: int = 8020

    database_url: str = "sqlite+aiosqlite:///./ai_orchestration.db"

    # Anthropic
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    anthropic_max_tokens: int = 1024

    # Downstream execution services this AI acts on. Only pricing-svc is
    # wired for the pilot retrofit; the rest are pre-registered so the
    # supervisor's routing table is complete even before each agent's
    # tool adapters are built out.
    pricing_svc_base_url: str = "http://pricing-svc:8010"
    fleet_svc_base_url: str = "http://fleet-svc:8004"
    subscription_svc_base_url: str = "http://subscription-svc:8003"
    crm_svc_base_url: str = "http://crm-svc:8021"
    rbac_svc_base_url: str = "http://rbac-svc:8011"
    identity_svc_base_url: str = "http://identity-svc:8014"
    marketing_svc_base_url: str = "http://marketing-svc:8007"
    rental_svc_base_url: str = "http://rental-svc:8005"
    dms_svc_base_url: str = "http://dms-svc:8008"
    hub_svc_base_url: str = "http://hub-svc:8006"
    org_svc_base_url: str = "http://org-svc:8022"
    hrm_svc_base_url: str = "http://hrm-svc:8023"

    # Revenue Agent — CRM-001 identity resolution. Mirrors crm-svc's own
    # threshold of the same name; kept as two settings in two services
    # deliberately (no shared config), same as every other cross-service
    # constant on this platform.
    duplicate_match_score_threshold: float = 0.72

    # Kafka
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_consumer_group: str = "ai-orchestration-svc"

    # Which Kafka consumer tier this process runs, if any. "none" is the
    # default and what every test and the plain REST-serving deployment
    # use — no Kafka connection is attempted at all. "realtime" and
    # "batch" each start exactly one of the two tiered consumer loops
    # from src/core/kafka_consumer.py as a background task alongside the
    # REST API. "all" runs both tiers in one process (local
    # docker-compose only). Never guess this from context — it is always
    # an explicit operator choice per deployment.
    consumer_role: str = "none"

    # Header the AI uses to identify itself to execution services so
    # audit logs can distinguish AI-driven actions from human-driven ones.
    ai_initiated_header_value: str = "ai-orchestration-svc"

    # Autonomy tier policy (docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md).
    # In Kubernetes this is a mounted ConfigMap at /etc/voyqi/tier-policy.yaml;
    # locally it defaults to the copy shipped in this service's own source
    # tree so no extra volume mount is needed to run it.
    tier_policy_path: str = "src/core/tier_policy.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
