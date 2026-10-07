import asyncio

import pytest

from src.core.config import get_settings
from src.main import app, lifespan


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_consumer_role_none_starts_no_background_task(monkeypatch):
    monkeypatch.setenv("AI_ORCH_CONSUMER_ROLE", "none")
    tasks_before = len(asyncio.all_tasks())

    async with lifespan(app):
        tasks_during = len(asyncio.all_tasks())

    assert tasks_during == tasks_before


async def test_consumer_role_realtime_starts_exactly_one_background_task(monkeypatch):
    monkeypatch.setenv("AI_ORCH_CONSUMER_ROLE", "realtime")

    started = []

    async def _fake_realtime_loop(session_factory):
        started.append("realtime")
        await asyncio.sleep(3600)  # stands in for a long-lived consumer loop

    monkeypatch.setattr("src.core.kafka_consumer.run_realtime_consumer_loop", _fake_realtime_loop)

    tasks_before = len(asyncio.all_tasks())
    async with lifespan(app):
        await asyncio.sleep(0)  # let the created task actually start running
        tasks_during = len(asyncio.all_tasks())
        assert tasks_during == tasks_before + 1
        assert started == ["realtime"]
    # the task is cancelled on exit — no leaked task after the context manager closes
    await asyncio.sleep(0)
    tasks_after = len(asyncio.all_tasks())
    assert tasks_after == tasks_before


async def test_consumer_role_batch_starts_the_batch_loop_not_realtime(monkeypatch):
    monkeypatch.setenv("AI_ORCH_CONSUMER_ROLE", "batch")

    started = []

    async def _fake_batch_loop(session_factory):
        started.append("batch")
        await asyncio.sleep(3600)

    async def _fail_if_called(session_factory):
        raise AssertionError("realtime loop should never start when consumer_role=batch")

    monkeypatch.setattr("src.core.kafka_consumer.run_batch_consumer_loop", _fake_batch_loop)
    monkeypatch.setattr("src.core.kafka_consumer.run_realtime_consumer_loop", _fail_if_called)

    async with lifespan(app):
        await asyncio.sleep(0)
        assert started == ["batch"]


async def test_consumer_role_all_starts_the_combined_loop(monkeypatch):
    """Local docker-compose runs one ai-orchestration container, so it
    needs both tiers in one process."""
    monkeypatch.setenv("AI_ORCH_CONSUMER_ROLE", "all")

    started = []

    async def _fake_all_loop(session_factory):
        started.append("all")
        await asyncio.sleep(3600)

    monkeypatch.setattr("src.core.kafka_consumer.run_all_consumer_loops", _fake_all_loop)

    async with lifespan(app):
        await asyncio.sleep(0)
        assert started == ["all"]


async def test_a_failed_consumer_loop_is_retried(monkeypatch):
    """Kafka is often not reachable yet when the pod starts — the
    consumer must reconnect rather than stay dead for the life of the
    process."""
    monkeypatch.setenv("AI_ORCH_CONSUMER_ROLE", "realtime")
    monkeypatch.setattr("src.main.CONSUMER_RETRY_INITIAL_SECONDS", 0)

    attempts = []

    async def _fails_twice_then_runs(session_factory):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("Kafka broker unreachable")
        await asyncio.sleep(3600)

    monkeypatch.setattr("src.core.kafka_consumer.run_realtime_consumer_loop", _fails_twice_then_runs)

    async with lifespan(app):
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(attempts) == 3


async def test_a_crashing_consumer_loop_never_propagates_out_of_lifespan(monkeypatch):
    """The REST API must keep serving even if the consumer loop dies —
    a dead Kafka connection is an ops problem, not a reason to take the
    whole service down."""
    monkeypatch.setenv("AI_ORCH_CONSUMER_ROLE", "realtime")

    async def _immediately_raises(session_factory):
        raise ConnectionError("Kafka broker unreachable")

    monkeypatch.setattr("src.core.kafka_consumer.run_realtime_consumer_loop", _immediately_raises)

    async with lifespan(app):
        await asyncio.sleep(0.01)  # let the failing task run and be caught
    # reaching this line at all is the assertion — no exception escaped lifespan
