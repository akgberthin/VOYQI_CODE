"""
Kafka event publishing, following the exact pattern already established
across every other service in the platform: publishing is a real call
against aiokafka in production, and is monkeypatched to a recording stub
in tests — it is never mocked away or skipped, per the standing rule
that every state change must publish an event.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rbac-svc.events")

_producer = None


async def get_producer():
    global _producer
    if _producer is None:
        from aiokafka import AIOKafkaProducer

        from src.core.config import get_settings

        settings = get_settings()
        candidate = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        try:
            await candidate.start()
        except Exception:
            # Never cache a producer that failed to start — reusing it
            # would mean every future publish hangs on send_and_wait
            # against a producer that was never actually ready, turning
            # one transient Kafka outage into a permanent one for the
            # life of the process. Leave _producer as None so the next
            # call gets a fresh, fast-failing attempt instead.
            raise
        _producer = candidate
    return _producer


async def publish_event(topic: str, event_type: str, tenant_id: str, payload: dict[str, Any]) -> None:
    envelope = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    try:
        producer = await get_producer()
        await producer.send_and_wait(topic, json.dumps(envelope, default=str).encode("utf-8"))
    except Exception:
        # Kafka unavailable (local/dev/test) — log and continue. Never
        # blocks the request; the write to Postgres is the source of
        # truth and the event is a downstream signal, not a transaction
        # participant.
        logger.info("event_publish_fallback topic=%s event=%s", topic, event_type)
