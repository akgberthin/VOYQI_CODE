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
    pricing_intelligence,
    recommendations,
    revenue_intelligence,
    talent,
)
from src.core.config import get_settings
from src.core.database import AsyncSessionLocal, init_models

logger = logging.getLogger("ai-orchestration-svc.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()

    settings = get_settings()
    consumer_task: asyncio.Task | None = None
    if settings.consumer_role in ("realtime", "batch"):
        from src.core.kafka_consumer import (
            run_batch_consumer_loop,
            run_realtime_consumer_loop,
        )

        loop_fn = run_realtime_consumer_loop if settings.consumer_role == "realtime" else run_batch_consumer_loop

        async def _guarded_consumer():
            try:
                await loop_fn(AsyncSessionLocal)
            except Exception:
                # A dead consumer should never take the REST API down
                # with it — log loudly and let the process keep serving
                # HTTP while ops investigates the Kafka connection.
                logger.exception("%s consumer loop exited unexpectedly", settings.consumer_role)

        consumer_task = asyncio.create_task(_guarded_consumer())

    yield

    if consumer_task is not None:
        consumer_task.cancel()


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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-orchestration-svc"}
