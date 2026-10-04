import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import employee_engine, workforce_analytics_engine

router = APIRouter(prefix="/api/v1/hrm", tags=["workforce-analytics"])


class AttritionRiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    risk_score: float
    reasoning: str


@router.post("/employees/{employee_id}/request-attrition-risk-assessment", status_code=202)
async def request_attrition_risk_assessment(
    employee_id: uuid.UUID, background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    # AI-first: called once by HR to kick off assessment (e.g. as part
    # of a periodic retention review), then evaluated automatically —
    # the same request/evaluate split as feedback drafting.
    factors = await workforce_analytics_engine.get_attrition_risk_factors(db, tenant_id, employee_id)
    background_tasks.add_task(
        publish_event,
        "hrm.workforce",
        "hrm.attrition_risk_requested",
        tenant_id,
        {"employee_id": str(employee_id), **factors},
    )
    return {"employee_id": str(employee_id), "status": "assessment_requested"}


@router.post("/employees/{employee_id}/assess-attrition-risk-now")
async def assess_attrition_risk_now(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Synchronous variant of the same AI-first evaluation the request
    endpoint already triggers asynchronously."""
    from src.services import ai_orchestration_tools

    employee = await employee_engine.get_employee(db, tenant_id, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    factors = await workforce_analytics_engine.get_attrition_risk_factors(db, tenant_id, employee_id)
    response = ai_orchestration_tools.request_attrition_risk(tenant_id, str(employee_id), factors)
    if response is None:
        raise HTTPException(status_code=502, detail="Talent Agent unreachable")
    return response


@router.post("/employees/{employee_id}/attrition-risk-scores", response_model=AttritionRiskOut, status_code=201)
async def record_attrition_risk(employee_id: uuid.UUID, risk_score: float, reasoning: str = "", tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.record_attrition_risk(db, tenant_id, employee_id, risk_score, reasoning)


@router.get("/employees/{employee_id}/attrition-risk-scores/latest", response_model=AttritionRiskOut | None)
async def get_latest_attrition_risk(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.get_latest_attrition_risk(db, tenant_id, employee_id)


@router.get("/attrition-risk-scores/watchlist", response_model=list[AttritionRiskOut])
async def get_attrition_risk_watchlist(threshold: float = 0.5, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.list_high_attrition_risk_employees(db, tenant_id, threshold)


@router.get("/analytics/headcount-by-department")
async def get_headcount_by_department(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.get_headcount_by_department(db, tenant_id)


@router.get("/analytics/headcount-by-entity")
async def get_headcount_by_entity(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.get_headcount_by_entity(db, tenant_id)


@router.get("/employees/{employee_id}/compensation-benchmark")
async def get_compensation_benchmark(employee_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.get_compensation_benchmark(db, tenant_id, employee_id)


@router.get("/analytics/pay-equity-snapshot")
async def get_pay_equity_snapshot(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await workforce_analytics_engine.get_pay_equity_snapshot(db, tenant_id)
