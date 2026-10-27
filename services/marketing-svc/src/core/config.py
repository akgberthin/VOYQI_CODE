from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MARKETING_", extra="ignore")

    service_name: str = "marketing-svc"
    port: int = 8007
    database_url: str = "sqlite+aiosqlite:///./marketing.db"
    kafka_bootstrap_servers: str = "kafka:9092"
    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"
    # Delivery goes through notification-svc, which holds the channel
    # providers (SMTP, Twilio, FCM) configured per tenant.
    notification_svc_base_url: str = "http://notification-svc:8027"
    # The scheduler executes sends whose time has come; 0 disables it.
    scheduler_interval_seconds: float = 30.0
    delivery_concurrency: int = 10

    # The one hard, structural gate in this entire service: a contact
    # on the suppression list (opted out, bounced, complained) can
    # never receive a send, on any channel, no override — the same
    # shape as OFAC elsewhere on this platform. TCPA (SMS/voice) and
    # CAN-SPAM (email) make this a legal requirement, not a judgment
    # call, so there is no threshold here and no bypass function
    # anywhere in send_engine.py.
    suppression_check_enabled: bool = True

    # Deterministic guardrail floor every AI-drafted piece of content
    # passes through before it can be auto-approved — brand-voice and
    # basic compliance checks, independent of whatever the live model
    # was asked to do. A draft that fails this is held for a human,
    # not silently sent; a draft that passes is auto-approved and
    # auto-scheduled with no further click.
    guardrail_max_claim_words: list[str] = ["guaranteed", "risk-free", "no risk", "cure", "free money"]
    guardrail_email_subject_max_chars: int = 150
    guardrail_sms_max_chars: int = 320

    # A/B tests conclude autonomously once a variant reaches
    # statistical significance at this minimum sample size per arm —
    # below it, evaluate_ab_test just reports "insufficient data" and
    # takes no action, it doesn't force an early call.
    ab_test_min_sample_per_arm: int = 100
    ab_test_significance_threshold: float = 0.05  # p-value

    # Calendar scheduling: minimum spacing (hours) enforced between
    # two sends to the same audience segment on the same channel, to
    # avoid autonomous over-messaging.
    min_hours_between_sends_same_segment: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
