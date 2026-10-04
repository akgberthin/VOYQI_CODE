"""
Kafka consumer entrypoints — realtime and batch tiers.

Two consumer processes run against the same codebase and the same
Postgres decision log — one logical AI service, not two independent
ones — but subscribing to different topic sets so throughput-heavy,
non-interactive reasoning (fleet, pricing, revenue, compliance, HRM's
domain agents) never competes with latency-sensitive, member-facing
reasoning (concierge chat replies, live upgrade-pattern checks) for a
node, a pod, or a shared Anthropic rate-limit budget.

The split is derived programmatically from supervisor.EVENT_ROUTING
rather than a second hand-maintained list — every event type is
classified by which agent it routes to, so adding a new event to the
routing table automatically places it on the correct tier with no
separate list to remember to update.
"""
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.services import concierge_agent, member_experience_agent
from src.services.supervisor import EVENT_ROUTING, route_event

logger = logging.getLogger("ai-orchestration-svc.consumer")

# The two agents whose reasoning is genuinely interactive — a person or
# a live member-facing flow is waiting on the result. Every other agent
# (fleet, pricing, revenue, compliance, HRM's domain agents, dealer
# intelligence, marketing content) is non-interactive backend reasoning
# and belongs on the batch tier by default.
REALTIME_AGENT_KEYS = {concierge_agent.AGENT_KEY, member_experience_agent.AGENT_KEY}


def classify_event_types() -> tuple[set[str], set[str]]:
    """Partitions every event type in the routing table into
    (realtime_topics, batch_topics). An event type routing to more than
    one agent (none currently do) is realtime if ANY of its handlers is
    a realtime agent, since that handler's latency needs still apply."""
    realtime: set[str] = set()
    batch: set[str] = set()
    for event_type, handlers in EVENT_ROUTING.items():
        agent_keys = {agent_key for agent_key, _handler in handlers}
        if agent_keys & REALTIME_AGENT_KEYS:
            realtime.add(event_type)
        else:
            batch.add(event_type)
    return realtime, batch


async def handle_raw_message(db: AsyncSession, raw_value: bytes) -> list:
    """Decodes one Kafka message envelope (as published by every execution
    service via publish_event) and routes it through the supervisor."""
    envelope = json.loads(raw_value.decode("utf-8"))
    event_type = envelope["event_type"]
    tenant_id = envelope["tenant_id"]
    payload = envelope["payload"]
    return await route_event(db, tenant_id, event_type, payload)


async def run_consumer_loop(db_session_factory, topics: list[str], group_id_suffix: str) -> None:  # pragma: no cover - live infra only
    """Shared implementation for both tiers — only the topic list and
    consumer group differ, so a realtime and a batch consumer never
    compete for the same partition assignment."""
    from aiokafka import AIOKafkaConsumer

    from src.core.config import get_settings

    settings = get_settings()
    if not topics:
        logger.warning("run_consumer_loop(%s) called with no topics — nothing to subscribe to.", group_id_suffix)
        return
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"{settings.kafka_consumer_group}-{group_id_suffix}",
    )
    await consumer.start()
    try:
        async for msg in consumer:
            async with db_session_factory() as db:
                await handle_raw_message(db, msg.value)
    finally:
        await consumer.stop()


async def run_realtime_consumer_loop(db_session_factory) -> None:  # pragma: no cover - live infra only
    realtime_topics, _batch_topics = classify_event_types()
    await run_consumer_loop(db_session_factory, sorted(realtime_topics), "realtime")


async def run_batch_consumer_loop(db_session_factory) -> None:  # pragma: no cover - live infra only
    _realtime_topics, batch_topics = classify_event_types()
    await run_consumer_loop(db_session_factory, sorted(batch_topics), "batch")
