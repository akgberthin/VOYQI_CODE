from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.ip_assets import router as ip_assets_router
from src.core.config import get_settings
from src.core.database import init_models

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="ip-asset-svc", lifespan=lifespan)
app.include_router(ip_assets_router, prefix="/api/v1/ip", tags=["ip-assets"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name}
