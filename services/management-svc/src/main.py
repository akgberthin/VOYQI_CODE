from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import alerts, cost_centers, dashboard, rollups, thresholds
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="management-svc", version="1.0.0", lifespan=lifespan)

# No frontend is planned for this service — consumed by the client's own
# integration layer and by other backend services — but CORS is left
# open to match the platform-wide default from every other service here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cost_centers.router)
app.include_router(rollups.router)
app.include_router(dashboard.router)
app.include_router(thresholds.router)
app.include_router(alerts.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "management-svc"}
