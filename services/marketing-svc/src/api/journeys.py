import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import channel_policy, journeys, reports

router = APIRouter(prefix="/api/v1/marketing", tags=["automation"])


class Step(BaseModel):
    delay_hours: int = 0
    channel: str
    subject: str = ""
    body: str


class JourneyIn(BaseModel):
    name: str
    trigger_event: str = "manual"
    steps: list[Step]


class JourneyPatch(BaseModel):
    name: str | None = None
    trigger_event: str | None = None
    status: str | None = None
    steps: list[Step] | None = None


class Contact(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    push_token: str = ""
    consent_voice: bool = False


class EnrollIn(BaseModel):
    contacts: list[Contact] = []
    segment_id: uuid.UUID | None = None


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: str
    phone: str
    current_step: int
    next_run_at: datetime | None
    status: str
    history: list
    source: str
    enrolled_at: datetime | None


def _journey(item) -> dict:
    j = item["journey"] if isinstance(item, dict) else item
    out = {"id": str(j.id), "name": j.name, "trigger_event": j.trigger_event, "status": j.status, "steps": j.steps, "created_by": j.created_by}
    if isinstance(item, dict):
        out["enrollments"] = item["enrollments"]
    return out


def _err(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404 if "not found" in str(exc) else 422, detail=str(exc))


@router.get("/journeys/templates")
async def templates():
    return journeys.TEMPLATES


@router.get("/journeys")
async def list_journeys(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return [_journey(i) for i in await journeys.list_journeys(db, tenant_id)]


@router.post("/journeys", status_code=201)
async def create_journey(body: JourneyIn, tenant_id: str = Depends(get_tenant_id), x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        return _journey(await journeys.create(db, tenant_id, body.name, body.trigger_event, [s.model_dump() for s in body.steps], x_user_id or "marketing-admin"))
    except journeys.JourneyError as exc:
        raise _err(exc) from exc


@router.patch("/journeys/{journey_id}")
async def update_journey(journey_id: uuid.UUID, body: JourneyPatch, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    values = body.model_dump(exclude_none=True)
    if body.steps is not None:
        values["steps"] = [s.model_dump() for s in body.steps]
    try:
        return _journey(await journeys.update_journey(db, tenant_id, journey_id, values))
    except journeys.JourneyError as exc:
        raise _err(exc) from exc


@router.post("/journeys/{journey_id}/enroll")
async def enroll(journey_id: uuid.UUID, body: EnrollIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Start the journey for these contacts or a whole segment (contacts
    already in it are skipped)."""
    try:
        journey = await journeys.get(db, tenant_id, journey_id)
        added = 0
        for c in body.contacts:
            if await journeys.enroll(db, tenant_id, journey, c.model_dump(), "manual"):
                added += 1
        if body.segment_id:
            added += await journeys.enroll_segment(db, tenant_id, journey_id, body.segment_id)
    except journeys.JourneyError as exc:
        raise _err(exc) from exc
    return {"enrolled": added}


@router.get("/journeys/{journey_id}/enrollments", response_model=list[EnrollmentOut])
async def list_enrollments(journey_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await journeys.enrollments(db, tenant_id, journey_id)


@router.post("/journeys/enrollments/{enrollment_id}/stop", response_model=EnrollmentOut)
async def stop(enrollment_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await journeys.stop(db, tenant_id, enrollment_id)
    except journeys.JourneyError as exc:
        raise _err(exc) from exc


class SettingsIn(BaseModel):
    timezone: str | None = None
    voice_window_start: int | None = None
    voice_window_end: int | None = None
    sms_window_start: int | None = None
    sms_window_end: int | None = None
    report_frequency: str | None = None
    report_recipients: list[str] | None = None


@router.get("/settings")
async def get_settings_(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    s = await channel_policy.get(db, tenant_id)
    return {"timezone": s.timezone, "voice_window_start": s.voice_window_start, "voice_window_end": s.voice_window_end,
            "voice_window_open_now": channel_policy.in_voice_window(s), "updated_by": s.updated_by,
            "sms_window_start": s.sms_window_start, "sms_window_end": s.sms_window_end, "sms_window_open_now": channel_policy.in_sms_window(s),
            "report_frequency": s.report_frequency, "report_recipients": s.report_recipients or [],
            "report_last_sent_at": s.report_last_sent_at}


@router.put("/settings")
async def save_settings(body: SettingsIn, tenant_id: str = Depends(get_tenant_id), x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        await channel_policy.save(db, tenant_id, body.model_dump(exclude_none=True), x_user_id or "marketing-admin")
    except channel_policy.PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await get_settings_(tenant_id, db)


@router.get("/reports")
async def report(days: int = 30, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await reports.build(db, tenant_id, max(1, min(days, 365)))


class ReportSendIn(BaseModel):
    days: int = 30
    recipients: list[str] | None = None


@router.post("/reports/send")
async def send_report(body: ReportSendIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Emails the report now — to the given people, or the saved recipients."""
    recipients = body.recipients or (await channel_policy.get(db, tenant_id)).report_recipients or []
    if not recipients:
        raise HTTPException(status_code=422, detail="Nobody to send it to: add report recipients first.")
    bad = [r for r in recipients if not channel_policy.EMAIL_RE.match(str(r).strip())]
    if bad:
        raise HTTPException(status_code=422, detail=f"Not an email address: {', '.join(bad)}")
    return await reports.send(db, tenant_id, max(1, min(body.days, 365)), [str(r).strip().lower() for r in recipients])


@router.get("/reports.csv")
async def report_csv(days: int = 30, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    data = reports.to_csv(await reports.build(db, tenant_id, max(1, min(days, 365))))
    return Response(content=data, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="marketing-report-{days}d.csv"'})
