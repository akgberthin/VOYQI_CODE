"""Runs scheduled sends and social posts when their time comes. Every
replica runs this loop; claiming makes sure each executes exactly once."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.core.config import get_settings
from src.services import autopilot, journeys, send_engine, social_engine

logger = logging.getLogger("marketing-svc.scheduler")


async def run_once(session_factory) -> int:
    ran = 0
    async with session_factory() as db:
        due = [(s.tenant_id, s.id) for s in await send_engine.due_sends(db)]
    for tenant_id, send_id in due:
        async with session_factory() as db:
            if not await send_engine.claim(db, tenant_id, send_id):
                continue  # another replica took it
            try:
                send = await send_engine.execute_send(db, tenant_id, send_id, claimed=True)
                if send.status.value == "scheduled":  # a call outside the calling window, moved to the next one
                    logger.info("scheduled send %s for %s waits for the calling window (%s)", send_id, tenant_id, send.scheduled_at)
                    continue
                logger.info("scheduled send %s for %s: %s", send_id, tenant_id, send.status.value)
                ran += 1
            except Exception:
                logger.exception("scheduled send %s failed", send_id)
    async with session_factory() as db:
        steps = await journeys.due(db)
    for enrollment_id in steps:
        async with session_factory() as db:
            enrollment = await journeys.claim(db, enrollment_id)
            if enrollment is None:
                continue
            try:
                await journeys.run_step(db, enrollment)
                ran += 1
            except Exception:
                logger.exception("journey step for enrollment %s failed; retrying in an hour", enrollment_id)
                await db.rollback()
                enrollment.claimed_at = None
                enrollment.next_run_at = datetime.now(timezone.utc) + timedelta(hours=1)
                await db.commit()
    async with session_factory() as db:
        planning = await autopilot.due_configs(db)
    for tenant_id in planning:
        async with session_factory() as db:
            try:
                planned = await autopilot.plan(db, tenant_id)
                logger.info("autopilot planned %d posts for %s", planned, tenant_id)
            except Exception:
                logger.exception("autopilot planning for %s failed", tenant_id)
    async with session_factory() as db:
        posts = [(p.tenant_id, p.id) for p in await social_engine.due_posts(db)]
    for tenant_id, post_id in posts:
        async with session_factory() as db:
            if not await social_engine.claim_post(db, tenant_id, post_id):
                continue
            try:
                post = await social_engine.publish_post(db, tenant_id, post_id, claimed=True)
                logger.info("scheduled post %s for %s: %s %s", post_id, tenant_id, post.status.value, post.delivery_mode)
                ran += 1
            except Exception:
                logger.exception("scheduled post %s failed", post_id)
    return ran


async def loop(session_factory) -> None:  # pragma: no cover - live loop
    interval = get_settings().scheduler_interval_seconds
    while True:
        try:
            await run_once(session_factory)
        except Exception:
            logger.exception("scheduler pass failed")
        await asyncio.sleep(interval)
