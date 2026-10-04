from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import (
    appraisals,
    compliance,
    deals,
    fi,
    franchise,
    inventory,
    lending,
    loaners,
    parts,
    performance,
    recon,
    rooftops,
    service,
)
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="dms-svc", version="1.0.0", lifespan=lifespan)

# The Dealer Center is a static HTML file calling this service
# directly from the browser — CORS has to be open here for that to
# work. Narrows to the gateway's origin in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rooftops.router)
app.include_router(inventory.router)
app.include_router(deals.router)
app.include_router(fi.router)
app.include_router(lending.router)
app.include_router(appraisals.router)
app.include_router(recon.router)
app.include_router(service.router)
app.include_router(parts.router)
app.include_router(loaners.router)
app.include_router(franchise.router)
app.include_router(performance.router)
app.include_router(compliance.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "dms-svc"}
