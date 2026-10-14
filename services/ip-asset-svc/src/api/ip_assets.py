import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import ip_engine

router = APIRouter()

ASSET_ERRORS = (ip_engine.AssetNotFoundError,)
STATUS_ERRORS = (ip_engine.InvalidAssetStatusTransitionError,)


class RegisterAssetIn(BaseModel):
    asset_type: str
    name: str
    owning_entity: str
    description: str | None = None
    jurisdiction: str | None = None
    registration_number: str | None = None
    filing_date: datetime | None = None


class UpdateStatusIn(BaseModel):
    new_status: str
    reason: str | None = None
    registration_number: str | None = None


class CreateLicenseIn(BaseModel):
    ip_asset_id: uuid.UUID
    licensee_entity: str
    license_type: str
    royalty_type: str = "none"
    royalty_rate: int = 0
    expiration_date: datetime | None = None
    terms: str | None = None


class TerminateLicenseIn(BaseModel):
    reason: str | None = None


class CreateStatementIn(BaseModel):
    license_agreement_id: uuid.UUID
    period_start: datetime
    period_end: datetime
    licensee_reported_revenue_cents: int | None = None


def _serialize_asset(a) -> dict:
    return {
        "id": a.id, "asset_type": a.asset_type, "name": a.name, "owning_entity": a.owning_entity,
        "status": a.status, "status_reason": a.status_reason, "registration_number": a.registration_number,
        "jurisdiction": a.jurisdiction, "grant_date": a.grant_date,
    }


def _serialize_license(lic) -> dict:
    return {
        "id": lic.id, "ip_asset_id": lic.ip_asset_id, "licensee_entity": lic.licensee_entity,
        "license_type": lic.license_type, "royalty_type": lic.royalty_type, "royalty_rate": lic.royalty_rate,
        "status": lic.status, "terminated_at": lic.terminated_at,
    }


def _serialize_statement(s) -> dict:
    return {
        "id": s.id, "license_agreement_id": s.license_agreement_id, "period_start": s.period_start, "period_end": s.period_end,
        "licensee_reported_revenue_cents": s.licensee_reported_revenue_cents, "royalty_owed_cents": s.royalty_owed_cents,
        "status": s.status, "finalized_at": s.finalized_at, "paid_at": s.paid_at,
    }


@router.post("/assets", status_code=201)
async def register_asset(payload: RegisterAssetIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    asset = await ip_engine.register_asset(db, tenant_id=tenant_id, **payload.model_dump())
    await db.commit()
    background_tasks.add_task(publish_event, "ip.assets", "ip.asset_registered", tenant_id, _serialize_asset(asset))
    return _serialize_asset(asset)


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        asset = await ip_engine.get_asset(db, tenant_id=tenant_id, asset_id=asset_id)
    except ASSET_ERRORS as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_asset(asset)


@router.post("/assets/{asset_id}/status")
async def update_asset_status(asset_id: uuid.UUID, payload: UpdateStatusIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        asset = await ip_engine.update_asset_status(db, tenant_id=tenant_id, asset_id=asset_id, **payload.model_dump())
    except ASSET_ERRORS as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except STATUS_ERRORS as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    background_tasks.add_task(publish_event, "ip.assets", "ip.asset_status_changed", tenant_id, _serialize_asset(asset))
    return _serialize_asset(asset)


@router.get("/assets")
async def list_assets_by_owning_entity(owning_entity: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    assets = await ip_engine.list_assets_by_owning_entity(db, tenant_id=tenant_id, owning_entity=owning_entity)
    return [_serialize_asset(a) for a in assets]


@router.post("/licenses", status_code=201)
async def create_license(payload: CreateLicenseIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        license_agreement = await ip_engine.create_license(db, tenant_id=tenant_id, **payload.model_dump())
    except ip_engine.AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ip_engine.AssetNotLicensableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ip_engine.ExclusiveLicenseConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    background_tasks.add_task(publish_event, "ip.licenses", "ip.license_created", tenant_id, _serialize_license(license_agreement))
    return _serialize_license(license_agreement)


@router.get("/licenses/{license_id}")
async def get_license(license_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        license_agreement = await ip_engine.get_license(db, tenant_id=tenant_id, license_id=license_id)
    except ip_engine.LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _serialize_license(license_agreement)


@router.post("/licenses/{license_id}/terminate")
async def terminate_license(license_id: uuid.UUID, payload: TerminateLicenseIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        license_agreement = await ip_engine.terminate_license(db, tenant_id=tenant_id, license_id=license_id, **payload.model_dump())
    except ip_engine.LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ip_engine.InvalidAssetStatusTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    background_tasks.add_task(publish_event, "ip.licenses", "ip.license_terminated", tenant_id, _serialize_license(license_agreement))
    return _serialize_license(license_agreement)


@router.get("/assets/{asset_id}/licenses")
async def list_active_licenses(asset_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    licenses = await ip_engine.list_active_licenses_for_asset(db, tenant_id=tenant_id, ip_asset_id=asset_id)
    return [_serialize_license(lic) for lic in licenses]


@router.post("/royalty-statements", status_code=201)
async def create_royalty_statement(payload: CreateStatementIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        statement = await ip_engine.create_royalty_statement(db, tenant_id=tenant_id, **payload.model_dump())
    except ip_engine.LicenseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ip_engine.MissingRevenueReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    background_tasks.add_task(publish_event, "ip.royalties", "ip.royalty_statement_created", tenant_id, _serialize_statement(statement))
    return _serialize_statement(statement)


@router.post("/royalty-statements/{statement_id}/finalize")
async def finalize_statement(statement_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        statement = await ip_engine.finalize_statement(db, tenant_id=tenant_id, statement_id=statement_id)
    except ip_engine.StatementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ip_engine.StatementAlreadyProcessedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    background_tasks.add_task(publish_event, "ip.royalties", "ip.royalty_statement_finalized", tenant_id, _serialize_statement(statement))
    return _serialize_statement(statement)


@router.post("/royalty-statements/{statement_id}/pay")
async def record_payment(statement_id: uuid.UUID, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        statement = await ip_engine.record_payment(db, tenant_id=tenant_id, statement_id=statement_id)
    except ip_engine.StatementNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ip_engine.StatementAlreadyProcessedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    background_tasks.add_task(publish_event, "ip.royalties", "ip.royalty_paid", tenant_id, _serialize_statement(statement))
    return _serialize_statement(statement)
