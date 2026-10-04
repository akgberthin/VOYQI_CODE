import json
import logging

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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka producer failed to start: %s", exc)
            _producer = None
    return _producer


async def publish_event(topic: str, payload: dict) -> None:
    try:
        producer = await get_producer()
        if producer is None:
            return
        await producer.send_and_wait(topic, json.dumps(payload, default=str).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to publish event to %s: %s", topic, exc)
