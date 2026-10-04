import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import performance_engine

router = APIRouter(prefix="/api/v1/dms/performance", tags=["performance"])


class PLSnapshotCreate(BaseModel):
    rooftop_id: uuid.UUID
    units: int
    gross_usd: float
    turn_rate: float
    fi_pvr_usd: float
    absorption_pct: float


class PLSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rooftop_id: uuid.UUID
    units: int
    gross_usd: float
    turn_rate: float
    fi_pvr_usd: float
    absorption_pct: float


@router.post("/pl-snapshots", response_model=PLSnapshotOut, status_code=201)
async def create_pl_snapshot(body: PLSnapshotCreate, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Every figure here is real transaction arithmetic — no AI
    reasoning belongs on a financial statement."""
    return await performance_engine.create_pl_snapshot(db, tenant_id, body.rooftop_id, body.units, body.gross_usd, body.turn_rate, body.fi_pvr_usd, body.absorption_pct)


@router.get("/pl-snapshots", response_model=list[PLSnapshotOut])
async def list_pl_snapshots(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await performance_engine.list_latest_snapshots(db, tenant_id)


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    rooftop_id: uuid.UUID
    verdict: str
    points: int
    reasons: list
    intervention_applied: bool


@router.post("/evaluate", response_model=list[EvaluationOut])
async def evaluate_dealer_performance(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Computes a per-rooftop verdict only — no downstream action.
    Use /evaluate-and-intervene for the full autonomous capstone."""
    return await performance_engine.evaluate_dealer_performance(db, tenant_id)


@router.post("/evaluate-and-intervene", response_model=list[EvaluationOut])
async def evaluate_and_apply_interventions(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The full capstone: computes the verdict AND autonomously
    triggers an accelerated inventory markdown sweep for any rooftop
    verdicted INTERVENE — a real cross-module chain, not just a
    dashboard label."""
    return await performance_engine.evaluate_and_apply_interventions(db, tenant_id)
