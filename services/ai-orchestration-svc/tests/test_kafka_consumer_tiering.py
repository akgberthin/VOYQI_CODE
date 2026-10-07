import json
import re

from src.core.kafka_consumer import (
    PLATFORM_TOPIC_PATTERN,
    REALTIME_AGENT_KEYS,
    classify_event_types,
    handle_raw_message,
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


def test_topic_pattern_matches_service_domain_topics():
    """Services publish to domain topics, not event-type topics — the
    consumer's subscription pattern has to match what they actually
    publish to."""
    for topic in ["fleet.fuel", "hrm.workforce", "subscription.lifecycle", "hub.operations", "originations.credit_decisions"]:
        assert re.match(PLATFORM_TOPIC_PATTERN, topic), topic


def test_topic_pattern_excludes_kafka_internal_topics():
    for topic in ["__consumer_offsets", "__transaction_state", "strimzi-topic-operator-kstreams-topic-store-changelog"]:
        assert not re.match(PLATFORM_TOPIC_PATTERN, topic), topic


def _envelope(event_type, payload=None):
    return json.dumps(
        {"event_type": event_type, "tenant_id": "tenant-1", "occurred_at": "2026-01-01T00:00:00+00:00", "payload": payload or {}}
    ).encode("utf-8")


async def test_handle_raw_message_skips_event_outside_this_tier(db_session, monkeypatch):
    called = []

    async def _fake_route(db, tenant_id, event_type, payload):
        called.append(event_type)
        return ["routed"]

    monkeypatch.setattr("src.core.kafka_consumer.route_event", _fake_route)
    realtime, _batch = classify_event_types()

    result = await handle_raw_message(db_session, _envelope("gu.fraud_suspected"), realtime)

    assert result == []
    assert called == []


async def test_handle_raw_message_routes_event_in_this_tier(db_session, monkeypatch):
    called = []

    async def _fake_route(db, tenant_id, event_type, payload):
        called.append((tenant_id, event_type, payload))
        return ["routed"]

    monkeypatch.setattr("src.core.kafka_consumer.route_event", _fake_route)
    _realtime, batch = classify_event_types()

    result = await handle_raw_message(db_session, _envelope("gu.fraud_suspected", {"k": 1}), batch)

    assert result == ["routed"]
    assert called == [("tenant-1", "gu.fraud_suspected", {"k": 1})]


async def test_handle_raw_message_skips_malformed_messages(db_session):
    """A raw payload with no envelope, or non-JSON bytes, is logged and
    skipped — it must never raise and kill the consumer loop."""
    assert await handle_raw_message(db_session, b"not json") == []
    assert await handle_raw_message(db_session, json.dumps({"id": "x"}).encode("utf-8")) == []
    assert await handle_raw_message(db_session, json.dumps([1, 2]).encode("utf-8")) == []


async def test_handle_raw_message_skips_event_already_evaluated_synchronously(db_session, monkeypatch):
    """subscription-svc asks the agent over HTTP for deposit captures and
    above-class requests, then publishes the same event — the consumer
    must not evaluate it a second time."""
    called = []

    async def _fake_route(db, tenant_id, event_type, payload):
        called.append(event_type)
        return ["routed"]

    monkeypatch.setattr("src.core.kafka_consumer.route_event", _fake_route)

    skipped = await handle_raw_message(db_session, _envelope("deposit.capture_requested", {"hold_id": "h", "ai_evaluated_synchronously": True}))
    routed = await handle_raw_message(db_session, _envelope("deposit.capture_requested", {"hold_id": "h", "ai_evaluated_synchronously": False}))

    assert skipped == []
    assert routed == ["routed"]
    assert called == ["deposit.capture_requested"]
