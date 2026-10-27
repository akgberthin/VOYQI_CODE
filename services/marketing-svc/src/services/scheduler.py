"""Runs scheduled sends when their time comes. Every replica runs this
loop; send_engine.claim makes sure each send executes exactly once."""
import asyncio
import logging

from src.core.config import get_settings
from src.services import send_engine

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
                logger.info("scheduled send %s for %s: %s", send_id, tenant_id, send.status.value)
                ran += 1
            except Exception:
                logger.exception("scheduled send %s failed", send_id)
    return ran


async def loop(session_factory) -> None:  # pragma: no cover - live loop
    interval = get_settings().scheduler_interval_seconds
    while True:
        try:
            await run_once(session_factory)
        except Exception:
            logger.exception("scheduler pass failed")
        await asyncio.sleep(interval)
