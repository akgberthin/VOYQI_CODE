import asyncio

import pytest

from src.core import kafka_events


@pytest.fixture(autouse=True)
def _reset_producer_cache():
    """Each test gets a clean slate — the module-level cache is exactly
    the thing this test suite is verifying the correctness of."""
    kafka_events._producer = None
    yield
    kafka_events._producer = None


async def test_failed_connection_does_not_cache_broken_producer(monkeypatch):
    """The actual bug this regresses: a failed AIOKafkaProducer.start()
    must not leave a half-started producer in the module cache, or
    every subsequent publish_event call hangs on send_and_wait against
    a producer that was never ready — turning one transient Kafka
    outage into a permanent one for the life of the process."""

    class _FailingProducer:
        def __init__(self, bootstrap_servers):
            pass

        async def start(self):
            raise ConnectionError("Kafka unreachable")

    monkeypatch.setattr("aiokafka.AIOKafkaProducer", _FailingProducer)

    await kafka_events.publish_event("topic", "event.one", "tenant-1", {})
    assert kafka_events._producer is None  # the core assertion: nothing broken got cached

    # A second call must also attempt a fresh connection, not hang
    # against the (nonexistent) cached broken producer.
    await asyncio.wait_for(kafka_events.publish_event("topic", "event.two", "tenant-1", {}), timeout=2.0)


async def test_successful_connection_is_cached_and_reused(monkeypatch):
    """The other half of correct behavior: a producer that DOES start
    successfully should be reused, not reconnected on every call."""
    start_call_count = 0

    class _WorkingProducer:
        def __init__(self, bootstrap_servers):
            pass

        async def start(self):
            nonlocal start_call_count
            start_call_count += 1

        async def send_and_wait(self, topic, payload):
            return None

    monkeypatch.setattr("aiokafka.AIOKafkaProducer", _WorkingProducer)

    await kafka_events.publish_event("topic", "event.one", "tenant-1", {})
    await kafka_events.publish_event("topic", "event.two", "tenant-1", {})
    assert start_call_count == 1  # started once, reused on the second call
    assert kafka_events._producer is not None


async def test_publish_event_never_raises_even_on_repeated_failures(monkeypatch):
    """publish_event's whole contract is that it never blocks or fails
    the request — verify that holds across repeated failures, not just
    the first one."""

    class _AlwaysFailingProducer:
        def __init__(self, bootstrap_servers):
            pass

        async def start(self):
            raise ConnectionError("Kafka unreachable")

    monkeypatch.setattr("aiokafka.AIOKafkaProducer", _AlwaysFailingProducer)

    for i in range(3):
        await asyncio.wait_for(kafka_events.publish_event("topic", f"event.{i}", "tenant-1", {}), timeout=2.0)
