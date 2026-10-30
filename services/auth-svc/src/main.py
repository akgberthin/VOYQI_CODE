import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.auth import router as auth_router
from src.api.internal import router as internal_router
from src.core.config import get_settings
from src.core.database import init_models

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.jwt_secret == "INSECURE-DEV-ONLY-CHANGE-ME" and not settings.allow_insecure_dev_secret:
        raise RuntimeError(
            "auth-svc refuses to start with the default JWT secret. Set "
            "AUTH_JWT_SECRET to a real secret, or explicitly set "
            "AUTH_ALLOW_INSECURE_DEV_SECRET=true for local development only."
        )
    await init_models()
    yield


app = FastAPI(title="auth-svc", lifespan=lifespan)
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(internal_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name}
