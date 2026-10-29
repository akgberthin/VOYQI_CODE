import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import (
    ab_tests,
    analytics,
    autopilot,
    audience,
    calendar,
    campaigns,
    content,
    journeys,
    lead_captures,
    library,
    media,
    media_generation,
    notes,
    seo,
    sends,
    social,
    suppression,
    tracking,
)
from src.core.config import get_settings
from src.core.database import AsyncSessionLocal, init_models
from src.core import event_consumer
from src.seo.common import SeoError
from src.seo.rules import RuleError
from src.services import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    settings = get_settings()
    tasks = []
    if settings.scheduler_interval_seconds > 0:
        tasks.append(asyncio.create_task(scheduler.loop(AsyncSessionLocal)))
    if settings.journey_events_enabled and not settings.database_url.startswith("sqlite"):
        tasks.append(asyncio.create_task(event_consumer.run(AsyncSessionLocal)))
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="marketing-svc", version="1.0.0", lifespan=lifespan)

# The marketing command center is a static HTML file calling this
# service directly from the browser — CORS has to be open here for
# that to work. Narrows to the gateway's origin in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(campaigns.router)
app.include_router(lead_captures.router)
app.include_router(content.router)
app.include_router(journeys.router)
app.include_router(library.router)
app.include_router(media.router)
app.include_router(notes.router)
app.include_router(audience.router)
app.include_router(suppression.router)
app.include_router(sends.router)
app.include_router(social.router)
app.include_router(calendar.router)
app.include_router(ab_tests.router)
app.include_router(analytics.router)
app.include_router(autopilot.router)
app.include_router(media_generation.router)
app.include_router(seo.router)
app.include_router(seo.public_router)
app.include_router(tracking.router)
app.include_router(tracking.public_router)
app.add_exception_handler(SeoError, seo.seo_error_handler)
app.add_exception_handler(RuleError, seo.rule_error_handler)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "marketing-svc"}
