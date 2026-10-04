
from src.core.kafka_consumer import (
    REALTIME_AGENT_KEYS,
    classify_event_types,
    run_consumer_loop,
)
from src.services import concierge_agent, member_experience_agent
from src.services.supervisor import EVENT_ROUTING


def test_every_routing_table_event_lands_on_exactly_one_tier():
    realtime, batch = classify_event_types()
    assert not (realtime & batch)
    assert (realtime | batch) == set(EVENT_ROUTING.keys())


def test_concierge_message_received_is_realtime():
    realtime, _batch = classify_event_types()
    assert "crm.message_received" in realtime


def test_above_class_request_is_realtime():
    """Member Experience Agent's upgrade-pattern check happens live as
    a member browses — it belongs on the realtime tier, not batch."""
    realtime, _batch = classify_event_types()
    assert "subscription.above_class_request_recorded" in realtime


def test_compliance_events_are_batch_not_realtime():
    """Compliance/security auditing is important but non-interactive —
    no member is waiting on a live response, so per the platform's own
    tiering rationale this belongs on batch, not realtime."""
    _realtime, batch = classify_event_types()
    assert "gu.fraud_suspected" in batch
    assert "deposit.capture_requested" in batch
    assert "role.assigned" in batch


def test_fleet_pricing_revenue_hrm_events_are_all_batch():
    _realtime, batch = classify_event_types()
    for event_type in ["subscription.created", "rental.booked", "lead.captured", "hrm.employee_created"]:
        assert event_type in batch


def test_realtime_agent_keys_are_exactly_concierge_and_member_experience():
    assert REALTIME_AGENT_KEYS == {concierge_agent.AGENT_KEY, member_experience_agent.AGENT_KEY}


def test_realtime_topic_count_is_small_relative_to_batch():
    """A sanity check on the shape of the split, not just its
    correctness — the whole point of tiering is that most reasoning on
    this platform is genuinely non-interactive."""
    realtime, batch = classify_event_types()
    assert len(realtime) < len(batch)


async def test_run_consumer_loop_with_no_topics_returns_without_starting_kafka(db_session, caplog):
    """A tier with zero classified topics (shouldn't happen today, but
    is a real possible future state) must not attempt to start an
    AIOKafkaConsumer with an empty topic list — it should just log a
    warning and return cleanly."""
    import logging

    async def _factory():
        return db_session

    with caplog.at_level(logging.WARNING, logger="ai-orchestration-svc.consumer"):
        await run_consumer_loop(_factory, [], "empty-tier-test")

    assert any("nothing to subscribe to" in record.message.lower() for record in caplog.records)

