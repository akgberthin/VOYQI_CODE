from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import alerts, dashboard, properties, rollups, thresholds
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="real-estate-svc", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(properties.router)
app.include_router(rollups.router)
app.include_router(dashboard.router)
app.include_router(thresholds.router)
app.include_router(alerts.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "real-estate-svc"}
