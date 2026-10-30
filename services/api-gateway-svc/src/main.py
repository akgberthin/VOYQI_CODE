from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response

from src.api.platform import router as platform_router
from src.api.proxy import router as proxy_router
from src.core.config import get_settings
from src.services import cors

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


@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    if not origin or not await cors.allowed(origin):
        return await call_next(request)
    if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
        return Response(status_code=204, headers=cors.headers(origin, preflight=True))
    response = await call_next(request)
    for k, v in cors.headers(origin, preflight=False).items():
        response.headers[k] = v
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name}


app.include_router(platform_router)

# Registered last: this is a catch-all, so anything more specific (like
# /health above) must be declared before it.
app.include_router(proxy_router)
