from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.proxy import router as proxy_router
from src.core.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.jwt_secret == "INSECURE-DEV-ONLY-CHANGE-ME" and not settings.allow_insecure_dev_secret:
        raise RuntimeError(
            "api-gateway-svc refuses to start with the default JWT secret. Set "
            "GATEWAY_JWT_SECRET to the SAME value as auth-svc's AUTH_JWT_SECRET, or "
            "explicitly set GATEWAY_ALLOW_INSECURE_DEV_SECRET=true for local development only."
        )
    yield


app = FastAPI(title="api-gateway-svc", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name}


# Registered last: this is a catch-all, so anything more specific (like
# /health above) must be declared before it.
app.include_router(proxy_router)
