import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer

from src.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_producer: AIOKafkaProducer | None = None


async def get_producer() -> AIOKafkaProducer | None:
    global _producer
    if _producer is None:
        try:
            _producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
            await _producer.start()
        except Exception as exc:  # noqa: BLE001 — fire-and-forget by design, see module docstring below
            logger.warning("Kafka producer failed to start: %s", exc)
            _producer = None
    return _producer


def build_envelope(event_type: str, payload: dict) -> tuple[str, dict]:
    """Wraps an event in the platform's standard envelope — the same
    {event_type, tenant_id, occurred_at, payload} shape fleet-svc,
    hrm-svc and subscription-svc publish, which ai-orchestration-svc's
    Kafka consumer expects. Returns (topic, envelope); the topic is the
    domain ("sales.order_created" -> "sales.events"), matching the
    platform convention of one topic per domain, not per event type."""
    body = dict(payload)
    tenant_id = body.pop("tenant_id")
    topic = f"{event_type.split('.')[0]}.events"
    envelope = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": body,
    }
    return topic, envelope


async def publish_event(event_type: str, payload: dict) -> None:
    """Fire-and-forget: logs and continues on failure rather than
    blocking the request that triggered it. `payload` must carry
    tenant_id; it is lifted into the envelope."""
    try:
        topic, envelope = build_envelope(event_type, payload)
        producer = await get_producer()
        if producer is None:
            return
        await producer.send_and_wait(topic, json.dumps(envelope, default=str).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to publish event %s: %s", event_type, exc)
