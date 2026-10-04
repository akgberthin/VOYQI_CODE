from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import analytics, fleet, hubs, insights, pricing, reallocation, reservations
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="rental-svc", version="1.0.0", lifespan=lifespan)

# Any rental-facing frontend calls this service directly from the
# browser — CORS has to be open here for that to work. Narrows to the
# gateway's origin in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fleet.router)
app.include_router(reservations.router)
app.include_router(hubs.router)
app.include_router(pricing.router)
app.include_router(insights.router)
app.include_router(reallocation.router)
app.include_router(analytics.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rental-svc"}
