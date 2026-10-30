import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import (
    agents,
    audit,
    concierge,
    dealer_intelligence,
    events,
    fleet_intelligence,
    goals,
    marketing_content,
    member_experience,
    policies,
    portfolio_intelligence,
    pricing_intelligence,
    recommendations,
    revenue_intelligence,
    seo,
    talent,
    tier_policy,
)
from src.core import integrations, prompts, tier_overrides
from src.core.config import get_settings
from src.core.database import AsyncSessionLocal, engine, init_models

logger = logging.getLogger("ai-orchestration-svc.main")

CONSUMER_RETRY_INITIAL_SECONDS = 2
CONSUMER_RETRY_MAX_SECONDS = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()

    settings = get_settings()
    consumer_task: asyncio.Task | None = None
    if settings.consumer_role in ("realtime", "batch", "all"):
        from src.core.kafka_consumer import (
            run_all_consumer_loops,
            run_batch_consumer_loop,
            run_realtime_consumer_loop,
        )

        loop_fn = {
            "realtime": run_realtime_consumer_loop,
            "batch": run_batch_consumer_loop,
            "all": run_all_consumer_loops,
        }[settings.consumer_role]

        async def _guarded_consumer():
            # A dead consumer should never take the REST API down with
            # it — log loudly and keep serving HTTP. It also must not stay
            # dead: Kafka is routinely not ready yet when this pod starts,
            # or restarts underneath it, so reconnect with capped backoff.
            delay = CONSUMER_RETRY_INITIAL_SECONDS
            while True:
                try:
                    await loop_fn(AsyncSessionLocal)
                    return
                except Exception:
                    logger.exception("%s consumer loop exited unexpectedly; retrying in %ss", settings.consumer_role, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, CONSUMER_RETRY_MAX_SECONDS)

        consumer_task = asyncio.create_task(_guarded_consumer())

    prompt_task: asyncio.Task | None = None
    if engine.url.get_backend_name() != "sqlite":
        # Pick up prompt versions (AI-003) and tier changes (AI-005) made
        # on other replicas.
        async def _refresh_prompts():
            while True:
                try:
                    async with AsyncSessionLocal() as db:
                        await prompts.refresh(db)
                        await tier_overrides.refresh(db)
                    await integrations.refresh()
                except Exception:
                    logger.exception("prompt/tier refresh failed; keeping the versions already loaded")
                await asyncio.sleep(prompts.PROMPT_REFRESH_SECONDS)

        prompt_task = asyncio.create_task(_refresh_prompts())

    yield

    if consumer_task is not None:
        consumer_task.cancel()
    if prompt_task is not None:
        prompt_task.cancel()


app = FastAPI(title="ai-orchestration-svc", version="1.0.0", lifespan=lifespan)

# The Super Admin Panel's AI Agent Control page is a static HTML file
# calling this service directly from the browser — same rationale as
# rbac-svc's CORS setup. Narrows to the gateway's origin in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Each router already declares its own full prefix (/api/v1/ai/...) — do
# not add another prefix here (double-prefix bug precedent elsewhere in
# the platform).
app.include_router(agents.router)
app.include_router(recommendations.router)
app.include_router(audit.router)
app.include_router(goals.router)
app.include_router(policies.router)
app.include_router(events.router)
app.include_router(marketing_content.router)
app.include_router(fleet_intelligence.router)
app.include_router(member_experience.router)
app.include_router(pricing_intelligence.router)
app.include_router(pricing_intelligence.dms_router)
app.include_router(revenue_intelligence.router)
app.include_router(revenue_intelligence.org_router)
app.include_router(concierge.router)
app.include_router(talent.router)
app.include_router(dealer_intelligence.router)
app.include_router(portfolio_intelligence.router)
app.include_router(tier_policy.router)
app.include_router(seo.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-orchestration-svc"}
