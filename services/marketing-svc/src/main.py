from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import (
    ab_tests,
    analytics,
    audience,
    calendar,
    campaigns,
    content,
    lead_captures,
    library,
    media_generation,
    notes,
    sends,
    social,
    suppression,
)
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


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
app.include_router(library.router)
app.include_router(notes.router)
app.include_router(audience.router)
app.include_router(suppression.router)
app.include_router(sends.router)
app.include_router(social.router)
app.include_router(calendar.router)
app.include_router(ab_tests.router)
app.include_router(analytics.router)
app.include_router(media_generation.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "marketing-svc"}
