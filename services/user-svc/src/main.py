from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.users import router as users_router
from src.core.config import get_settings
from src.core.database import init_models

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="user-svc", lifespan=lifespan)
app.include_router(users_router, prefix="/api/v1/user", tags=["users"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name}
