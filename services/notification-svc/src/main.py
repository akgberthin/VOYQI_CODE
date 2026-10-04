from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.notifications import router as notifications_router
from src.core.config import get_settings
from src.core.database import AsyncSessionLocal, init_models
from src.services.template_seed import seed_default_templates

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    async with AsyncSessionLocal() as session:  # type: AsyncSession
        await seed_default_templates(session, tenant_id="voyqi-holdings")
        await session.commit()
    yield


app = FastAPI(title="notification-svc", lifespan=lifespan)
app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["notifications"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.service_name}
