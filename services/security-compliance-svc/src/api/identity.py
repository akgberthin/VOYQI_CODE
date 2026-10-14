"""Privileged-access sessions under /api/v1/identity — the paths
ai-orchestration-svc's identity_tools already calls to end a session
once a human approves the Compliance/Audit Agent's recommendation."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import AccessSessionStatus, BreakGlassSession, ImpersonationSession
from src.services import access_session_engine as engine

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])


class BreakGlassIn(BaseModel):
    user_id: str
    activated_by: str
    reason: str
    duration_minutes: int = 30


class BreakGlassOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: str
    activated_by: str
    reason: str
    duration_minutes: int
    off_hours: bool
    status: AccessSessionStatus
    started_at: datetime
    expires_at: datetime
    ended_at: datetime | None
    ended_by: str | None
    end_reason: str | None


class ImpersonationIn(BaseModel):
    admin_user_id: str
    target_user_id: str
    reason: str
    session_recording_ref: str | None = None


class ImpersonationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    admin_user_id: str
    target_user_id: str
    reason: str
    session_recording_ref: str | None
    off_hours: bool
    status: AccessSessionStatus
    started_at: datetime
    ended_at: datetime | None
    ended_by: str | None
    end_reason: str | None


class EndSessionIn(BaseModel):
    ended_by: str
    reason: str = ""


@router.post("/break-glass", response_model=BreakGlassOut, status_code=201)
async def start_break_glass(body: BreakGlassIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await engine.start_break_glass(db, tenant_id, body.user_id, body.activated_by, body.reason, body.duration_minutes)
    except (engine.ReasonRequiredError, engine.InvalidDurationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/break-glass", response_model=list[BreakGlassOut])
async def list_break_glass(status: AccessSessionStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_sessions(db, BreakGlassSession, tenant_id, status)


@router.post("/break-glass/{session_id}/end", response_model=BreakGlassOut)
async def end_break_glass(session_id: uuid.UUID, body: EndSessionIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        session = await engine.end_break_glass(db, tenant_id, session_id, body.ended_by, body.reason)
    except engine.SessionAlreadyEndedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="Break-glass session not found")
    return session


@router.post("/impersonation", response_model=ImpersonationOut, status_code=201)
async def start_impersonation(body: ImpersonationIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await engine.start_impersonation(db, tenant_id, body.admin_user_id, body.target_user_id, body.reason, body.session_recording_ref)
    except engine.ReasonRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/impersonation", response_model=list[ImpersonationOut])
async def list_impersonation(status: AccessSessionStatus | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_sessions(db, ImpersonationSession, tenant_id, status)


@router.post("/impersonation/{session_id}/end", response_model=ImpersonationOut)
async def end_impersonation(session_id: uuid.UUID, body: EndSessionIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        session = await engine.end_impersonation(db, tenant_id, session_id, body.ended_by, body.reason)
    except engine.SessionAlreadyEndedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(status_code=404, detail="Impersonation session not found")
    return session
