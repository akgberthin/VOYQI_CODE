"""
MQTT subscriber that feeds device messages to services/mqtt_ingest.

Runs inside fleet-svc when FLEET_MQTT_HOST is set. Every replica joins
the same shared subscription ($share/fleet-svc/...), so the broker hands
each message to exactly one replica. The connection is retried with
backoff for the life of the process — a broker outage delays ingest,
it never takes the HTTP API down with it.
"""
import asyncio
import logging
import ssl
import uuid

from src.core.config import get_settings
from src.core.database import AsyncSessionLocal
from src.services import mqtt_ingest

logger = logging.getLogger("fleet-svc.mqtt")


def _tls_context(settings) -> ssl.SSLContext | None:
    if not settings.mqtt_tls_ca_file:
        return None
    context = ssl.create_default_context(cafile=settings.mqtt_tls_ca_file)
    if settings.mqtt_tls_cert_file:
        context.load_cert_chain(settings.mqtt_tls_cert_file, settings.mqtt_tls_key_file or None)
    return context


async def run() -> None:
    import aiomqtt

    settings = get_settings()
    backoff = 1.0
    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.mqtt_host, port=settings.mqtt_port,
                identifier=f"fleet-svc-{uuid.uuid4().hex[:8]}",
                username=settings.mqtt_username or None, password=settings.mqtt_password or None,
                tls_context=_tls_context(settings),
            ) as client:
                await client.subscribe(settings.mqtt_subscription, qos=1)
                logger.info("subscribed to %s on %s:%s", settings.mqtt_subscription, settings.mqtt_host, settings.mqtt_port)
                backoff = 1.0
                async for message in client.messages:
                    try:
                        async with AsyncSessionLocal() as db:
                            await mqtt_ingest.handle_message(db, str(message.topic), bytes(message.payload or b""))
                    except Exception:
                        logger.exception("failed to ingest message on %s", message.topic)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MQTT connection lost (%s); retrying in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
