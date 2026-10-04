from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import (
    billing,
    black_concierge,
    continuity,
    corporate,
    deposit_holds,
    enrollment,
    gu_economy,
    lifecycle,
    members_plans,
    upgrade_intelligence,
    vehicle_swap,
)
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="subscription-svc", version="1.0.0", lifespan=lifespan)

# The personal and corporate member portals are static HTML files
# calling this service directly from the browser — CORS has to be
# open here for that to work at all. Narrows to the gateway's origin
# only in production, per the platform's gateway-only-ingress principle.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(members_plans.router)
app.include_router(enrollment.router)
app.include_router(lifecycle.router)
app.include_router(billing.router)
app.include_router(gu_economy.router)
app.include_router(vehicle_swap.router)
app.include_router(continuity.router)
app.include_router(black_concierge.router)
app.include_router(corporate.router)
app.include_router(deposit_holds.router)
app.include_router(upgrade_intelligence.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "subscription-svc"}
