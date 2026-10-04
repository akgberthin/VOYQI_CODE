from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import analytics, hubs, ops_board
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="hub-svc", version="1.0.0", lifespan=lifespan)

app.include_router(hubs.router)
app.include_router(ops_board.router)
app.include_router(analytics.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "hub-svc"}
