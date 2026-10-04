from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import (
    benefits,
    case_management,
    email,
    employees,
    notices,
    payroll,
    performance,
    recruiting,
    training,
    workforce_analytics,
)
from src.core.database import init_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="hrm-svc", version="1.0.0", lifespan=lifespan)

app.include_router(employees.router)
app.include_router(payroll.router)
app.include_router(benefits.router)
app.include_router(recruiting.router)
app.include_router(performance.router)
app.include_router(workforce_analytics.router)
app.include_router(training.router)
app.include_router(case_management.router)
app.include_router(notices.router)
app.include_router(email.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "hrm-svc"}
