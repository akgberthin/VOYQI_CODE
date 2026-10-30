from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import abac, audit, content, integrations, media, modules, rbac, sections, security_policy, settings, tenants, theme
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="rbac-svc", version="1.0.0", lifespan=lifespan)

# The Super Admin Panel is a static HTML file with no build step or
# gateway in front of it in this environment, so it calls this service
# directly from the browser — CORS has to be open here for that to
# work at all. In production this narrows to the gateway's origin
# only, per the platform's gateway-only-ingress principle.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tenants.router)
app.include_router(rbac.router)
app.include_router(abac.router)
app.include_router(modules.router)
app.include_router(audit.router)
app.include_router(settings.router)
app.include_router(content.router)
app.include_router(sections.router)
app.include_router(theme.router)
app.include_router(media.router)
app.include_router(integrations.router)
app.include_router(integrations.internal)
app.include_router(security_policy.router)
app.include_router(security_policy.internal)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rbac-svc"}
