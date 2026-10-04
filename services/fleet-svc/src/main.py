from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import (
    analytics,
    depreciation,
    ev_charging,
    floorplan,
    fuel,
    incidents,
    inventory,
    maintenance,
    remarketing,
    safety_scoring,
    staging,
    telematics,
)
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="fleet-svc", version="1.0.0", lifespan=lifespan)

app.include_router(inventory.router)
app.include_router(staging.router)
app.include_router(maintenance.router)
app.include_router(incidents.router)
app.include_router(telematics.router)
app.include_router(depreciation.router)
app.include_router(remarketing.router)
app.include_router(floorplan.router)
app.include_router(safety_scoring.router)
app.include_router(fuel.router)
app.include_router(ev_charging.router)
app.include_router(analytics.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fleet-svc"}
