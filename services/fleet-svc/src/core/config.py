from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLEET_", extra="ignore")

    service_name: str = "fleet-svc"
    port: int = 8004
    database_url: str = "sqlite+aiosqlite:///./fleet.db"
    kafka_bootstrap_servers: str = "kafka:9092"

    # FLT-004 — mileage since last service beyond this triggers a
    # predictive maintenance alert, independent of any DTC codes present.
    maintenance_mileage_interval: int = 5000

    # FLT-007 — disposition eligibility thresholds.
    disposition_age_months: int = 36
    disposition_mileage: int = 60000
    # FLT-012 — state-of-health below this triggers a battery
    # degradation alert. 80% is the commonly-cited industry threshold
    # for meaningfully reduced range/capacity.
    battery_health_alert_threshold_pct: float = 80.0

    ai_orchestration_svc_base_url: str = "http://ai-orchestration-svc:8020"

    # Device telemetry over MQTT (services/mqtt_ingest.py). Empty host
    # disables the subscriber; the HTTP ingest endpoints work either way.
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_subscription: str = "$share/fleet-svc/voyqi/+/vehicles/+/+"
    mqtt_tls_ca_file: str = ""
    mqtt_tls_cert_file: str = ""
    mqtt_tls_key_file: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
