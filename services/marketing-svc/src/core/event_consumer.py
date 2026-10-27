"""Platform events that can start an automation journey (user.created,
lead.captured, subscription.cancelled, crm.person_profile_created, …).

Starts from the latest offset: a new deployment doesn't enrol everyone
who ever signed up. Offsets are committed after each event is handled."""
import asyncio
import json
import logging

from src.core.config import get_settings
from src.services import journeys

logger = logging.getLogger("marketing-svc.events")
TOPIC_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$"


async def handle(session_factory, raw: bytes) -> int:
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return 0
    event_type, tenant_id, payload = envelope.get("event_type"), envelope.get("tenant_id"), envelope.get("payload") or {}
    if not (event_type and tenant_id and isinstance(payload, dict)) or not (payload.get("email") or payload.get("phone")):
        return 0
    async with session_factory() as db:
        return await journeys.on_event(db, tenant_id, event_type, payload)


async def run(session_factory) -> None:  # pragma: no cover - live Kafka
    from aiokafka import AIOKafkaConsumer

    backoff = 2.0
    while True:
        consumer = AIOKafkaConsumer(bootstrap_servers=get_settings().kafka_bootstrap_servers, group_id="marketing-journeys",
                                    auto_offset_reset="latest", enable_auto_commit=False, metadata_max_age_ms=30_000)
        try:
            consumer.subscribe(pattern=TOPIC_PATTERN)
            await consumer.start()
            backoff = 2.0
            async for msg in consumer:
                try:
                    started = await handle(session_factory, msg.value)
                    if started:
                        logger.info("event on %s started %d journey(s)", msg.topic, started)
                except Exception:
                    logger.exception("journey enrolment from %s failed", msg.topic)
                await consumer.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("journey event consumer stopped (%s); retrying in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        finally:
            await consumer.stop()
