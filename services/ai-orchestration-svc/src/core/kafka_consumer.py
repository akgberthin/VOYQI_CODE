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
import asyncio
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


# Execution services publish to domain-level topics ("fleet.fuel",
# "hrm.workforce", "subscription.lifecycle") with the specific event type
# carried inside the envelope — topic names and event types are NOT the
# same strings. So each tier subscribes to every platform topic by
# pattern and filters on the envelope's event_type, rather than
# subscribing to event-type-named topics no service ever publishes to.
# Platform topics are "<domain>.<name>"; Kafka/Strimzi internals
# ("__consumer_offsets", "strimzi-...") never match.
PLATFORM_TOPIC_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$"


async def handle_raw_message(db: AsyncSession, raw_value: bytes, event_types: set[str] | None = None) -> list:
    """Decodes one Kafka message envelope (as published by every execution
    service via publish_event) and routes it through the supervisor.

    `event_types` restricts handling to one tier's classified events —
    anything else on the topic belongs to the other tier (or to no agent)
    and is skipped. A malformed message is logged and skipped, never
    raised: one bad publisher must not kill the consumer loop for every
    other service's events."""
    try:
        envelope = json.loads(raw_value.decode("utf-8"))
        event_type = envelope["event_type"]
        tenant_id = envelope["tenant_id"]
        payload = envelope["payload"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("Skipping malformed event envelope: %s", exc)
        return []
    if event_types is not None and event_type not in event_types:
        return []
    # Some services ask an agent synchronously over HTTP (the member is
    # waiting on the answer) and also publish the event for the record.
    # They flag those so the agent doesn't evaluate the same thing twice.
    if isinstance(payload, dict) and payload.get("ai_evaluated_synchronously") is True:
        return []
    return await route_event(db, tenant_id, event_type, payload)


async def run_consumer_loop(db_session_factory, event_types: list[str], group_id_suffix: str) -> None:  # pragma: no cover - live infra only
    """Shared implementation for both tiers — only the handled event
    types and the consumer group differ. Each tier is its own consumer
    group, so both see every message and each handles only its own
    classified events."""
    from aiokafka import AIOKafkaConsumer

    from src.core.config import get_settings

    settings = get_settings()
    if not event_types:
        logger.warning("run_consumer_loop(%s) called with no event types — nothing to subscribe to.", group_id_suffix)
        return
    handled = set(event_types)
    consumer = AIOKafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=f"{settings.kafka_consumer_group}-{group_id_suffix}",
        # Pick up a newly created service topic within ~30s instead of
        # the 5-minute default.
        metadata_max_age_ms=30_000,
        # A topic is usually created by its first publish, before this
        # consumer has seen it — "latest" would silently skip that first
        # event. With committed offsets, this only applies to partitions
        # the group has never read.
        auto_offset_reset="earliest",
        # Offsets are committed only after an event has been handled, so
        # a pod killed mid-event gets it redelivered instead of losing it
        # (at-least-once; auto-commit could commit ahead of processing).
        enable_auto_commit=False,
    )
    consumer.subscribe(pattern=PLATFORM_TOPIC_PATTERN)
    await consumer.start()
    try:
        async for msg in consumer:
            async with db_session_factory() as db:
                try:
                    await handle_raw_message(db, msg.value, handled)
                except Exception:
                    # An agent failure on one event must not stop every
                    # later event from being processed.
                    logger.exception("%s consumer failed handling a message from %s", group_id_suffix, msg.topic)
                    await db.rollback()
            await consumer.commit()
    finally:
        await consumer.stop()


async def run_realtime_consumer_loop(db_session_factory) -> None:  # pragma: no cover - live infra only
    realtime_events, _batch_events = classify_event_types()
    await run_consumer_loop(db_session_factory, sorted(realtime_events), "realtime")


async def run_batch_consumer_loop(db_session_factory) -> None:  # pragma: no cover - live infra only
    _realtime_events, batch_events = classify_event_types()
    await run_consumer_loop(db_session_factory, sorted(batch_events), "batch")


async def run_all_consumer_loops(db_session_factory) -> None:  # pragma: no cover - live infra only
    """Both tiers in one process — for local docker-compose, where there
    is a single ai-orchestration container. Production runs each tier as
    its own Deployment instead. A TaskGroup, not gather: if one tier
    fails, the other is cancelled too, so the caller's retry restarts
    both instead of leaving a duplicate consumer running."""
    async with asyncio.TaskGroup() as group:
        group.create_task(run_realtime_consumer_loop(db_session_factory))
        group.create_task(run_batch_consumer_loop(db_session_factory))
