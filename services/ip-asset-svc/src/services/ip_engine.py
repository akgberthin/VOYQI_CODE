"""IP asset engine.

Two genuinely different lifecycles live in one table on purpose: a
patent/trademark/copyright goes through a filing process (pending ->
granted, or -> abandoned), while a trade secret or unregistered
proprietary asset is protected by confidentiality from the moment it's
recorded and only ever has one possible bad outcome — compromise
(disclosure) — which is one-way and terminal, same as a granted IP
asset's expiration. Mixing these transition rules up (e.g. letting a
trade secret be "granted") would misrepresent what actually protects
that asset legally.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    LICENSABLE_STATUSES,
    REGISTERABLE_TYPES,
    TERMINAL_STATUSES,
    AssetStatus,
    IPAsset,
    LicenseAgreement,
    LicenseStatus,
    LicenseType,
    RoyaltyStatement,
    RoyaltyStatementStatus,
    RoyaltyType,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class AssetNotFoundError(Exception):
    pass


class LicenseNotFoundError(Exception):
    pass


class StatementNotFoundError(Exception):
    pass


class InvalidAssetStatusTransitionError(Exception):
    pass


class AssetNotLicensableError(Exception):
    pass


class ExclusiveLicenseConflictError(Exception):
    pass


class StatementAlreadyProcessedError(Exception):
    pass


class MissingRevenueReportError(Exception):
    pass


async def register_asset(
    db: AsyncSession, *, tenant_id: str, asset_type: str, name: str, owning_entity: str,
    description: str | None = None, jurisdiction: str | None = None, registration_number: str | None = None,
    filing_date: datetime | None = None,
) -> IPAsset:
    initial_status = AssetStatus.PENDING.value if asset_type in REGISTERABLE_TYPES else AssetStatus.PROTECTED.value
    asset = IPAsset(
        tenant_id=tenant_id, asset_type=asset_type, name=name, description=description, owning_entity=owning_entity,
        jurisdiction=jurisdiction, registration_number=registration_number, filing_date=filing_date, status=initial_status,
    )
    db.add(asset)
    await db.flush()
    return asset


async def get_asset(db: AsyncSession, *, tenant_id: str, asset_id: uuid.UUID) -> IPAsset:
    result = await db.execute(select(IPAsset).where(IPAsset.id == asset_id, IPAsset.tenant_id == tenant_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        raise AssetNotFoundError(f"IP asset {asset_id} not found")
    return asset


async def update_asset_status(
    db: AsyncSession, *, tenant_id: str, asset_id: uuid.UUID, new_status: str, reason: str | None = None,
    registration_number: str | None = None,
) -> IPAsset:
    asset = await get_asset(db, tenant_id=tenant_id, asset_id=asset_id)

    if asset.status in TERMINAL_STATUSES:
        raise InvalidAssetStatusTransitionError(f"asset {asset_id} is {asset.status}, a terminal state — no further transitions allowed")

    is_registerable = asset.asset_type in REGISTERABLE_TYPES
    valid_targets = (
        {AssetStatus.GRANTED.value, AssetStatus.ABANDONED.value, AssetStatus.EXPIRED.value} if is_registerable
        else {AssetStatus.COMPROMISED.value}
    )
    if new_status not in valid_targets:
        raise InvalidAssetStatusTransitionError(
            f"'{new_status}' is not a valid transition for a {asset.asset_type} asset currently {asset.status} "
            f"(valid targets: {sorted(valid_targets)})"
        )

    if new_status == AssetStatus.GRANTED.value:
        asset.grant_date = utcnow()
        if registration_number:
            asset.registration_number = registration_number

    asset.status = new_status
    asset.status_reason = reason
    await db.flush()
    return asset


async def create_license(
    db: AsyncSession, *, tenant_id: str, ip_asset_id: uuid.UUID, licensee_entity: str, license_type: str,
    royalty_type: str = RoyaltyType.NONE.value, royalty_rate: int = 0, expiration_date: datetime | None = None, terms: str | None = None,
) -> LicenseAgreement:
    asset = await get_asset(db, tenant_id=tenant_id, asset_id=ip_asset_id)
    if asset.status not in LICENSABLE_STATUSES:
        raise AssetNotLicensableError(f"asset {ip_asset_id} is {asset.status}, not in a licensable state")

    result = await db.execute(select(LicenseAgreement).where(LicenseAgreement.ip_asset_id == ip_asset_id, LicenseAgreement.status == LicenseStatus.ACTIVE.value))
    active_licenses = result.scalars().all()

    has_active_exclusive = any(lic.license_type == LicenseType.EXCLUSIVE.value for lic in active_licenses)
    if has_active_exclusive:
        raise ExclusiveLicenseConflictError(f"asset {ip_asset_id} already has an active exclusive license — no further licenses can be granted while it's in effect")
    if license_type == LicenseType.EXCLUSIVE.value and len(active_licenses) > 0:
        raise ExclusiveLicenseConflictError(f"asset {ip_asset_id} already has {len(active_licenses)} active non-exclusive license(s) — cannot grant an exclusive license while they're in effect")

    license_agreement = LicenseAgreement(
        tenant_id=tenant_id, ip_asset_id=ip_asset_id, licensee_entity=licensee_entity, license_type=license_type,
        royalty_type=royalty_type, royalty_rate=royalty_rate, expiration_date=expiration_date, terms=terms,
    )
    db.add(license_agreement)
    await db.flush()
    return license_agreement


async def get_license(db: AsyncSession, *, tenant_id: str, license_id: uuid.UUID) -> LicenseAgreement:
    result = await db.execute(select(LicenseAgreement).where(LicenseAgreement.id == license_id, LicenseAgreement.tenant_id == tenant_id))
    license_agreement = result.scalar_one_or_none()
    if license_agreement is None:
        raise LicenseNotFoundError(f"license {license_id} not found")
    return license_agreement


async def terminate_license(db: AsyncSession, *, tenant_id: str, license_id: uuid.UUID, reason: str | None = None) -> LicenseAgreement:
    license_agreement = await get_license(db, tenant_id=tenant_id, license_id=license_id)
    if license_agreement.status != LicenseStatus.ACTIVE.value:
        raise InvalidAssetStatusTransitionError(f"license {license_id} is {license_agreement.status}, not active")
    license_agreement.status = LicenseStatus.TERMINATED.value
    license_agreement.terminated_at = utcnow()
    license_agreement.termination_reason = reason
    await db.flush()
    return license_agreement


def _calculate_royalty_owed(license_agreement: LicenseAgreement, licensee_reported_revenue_cents: int | None) -> int:
    if license_agreement.royalty_type == RoyaltyType.FIXED_FEE.value:
        return license_agreement.royalty_rate
    if license_agreement.royalty_type == RoyaltyType.NONE.value:
        return 0
    # percentage_of_revenue
    if licensee_reported_revenue_cents is None:
        raise MissingRevenueReportError("this license's royalty is revenue-based — licensee_reported_revenue_cents is required")
    return (licensee_reported_revenue_cents * license_agreement.royalty_rate) // 10000


async def create_royalty_statement(
    db: AsyncSession, *, tenant_id: str, license_agreement_id: uuid.UUID, period_start: datetime, period_end: datetime,
    licensee_reported_revenue_cents: int | None = None,
) -> RoyaltyStatement:
    license_agreement = await get_license(db, tenant_id=tenant_id, license_id=license_agreement_id)
    royalty_owed_cents = _calculate_royalty_owed(license_agreement, licensee_reported_revenue_cents)

    statement = RoyaltyStatement(
        tenant_id=tenant_id, license_agreement_id=license_agreement_id, period_start=period_start, period_end=period_end,
        licensee_reported_revenue_cents=licensee_reported_revenue_cents, royalty_owed_cents=royalty_owed_cents,
    )
    db.add(statement)
    await db.flush()
    return statement


async def get_statement(db: AsyncSession, *, tenant_id: str, statement_id: uuid.UUID) -> RoyaltyStatement:
    result = await db.execute(select(RoyaltyStatement).where(RoyaltyStatement.id == statement_id, RoyaltyStatement.tenant_id == tenant_id))
    statement = result.scalar_one_or_none()
    if statement is None:
        raise StatementNotFoundError(f"royalty statement {statement_id} not found")
    return statement


async def finalize_statement(db: AsyncSession, *, tenant_id: str, statement_id: uuid.UUID) -> RoyaltyStatement:
    statement = await get_statement(db, tenant_id=tenant_id, statement_id=statement_id)
    if statement.status != RoyaltyStatementStatus.DRAFT.value:
        raise StatementAlreadyProcessedError(f"statement {statement_id} is {statement.status}, not draft")
    statement.status = RoyaltyStatementStatus.FINALIZED.value
    statement.finalized_at = utcnow()
    await db.flush()
    return statement


async def record_payment(db: AsyncSession, *, tenant_id: str, statement_id: uuid.UUID) -> RoyaltyStatement:
    statement = await get_statement(db, tenant_id=tenant_id, statement_id=statement_id)
    if statement.status != RoyaltyStatementStatus.FINALIZED.value:
        raise StatementAlreadyProcessedError(f"statement {statement_id} is {statement.status} — must be finalized before it can be paid")
    statement.status = RoyaltyStatementStatus.PAID.value
    statement.paid_at = utcnow()
    await db.flush()
    return statement


async def list_assets_by_owning_entity(db: AsyncSession, *, tenant_id: str, owning_entity: str) -> list[IPAsset]:
    result = await db.execute(select(IPAsset).where(IPAsset.tenant_id == tenant_id, IPAsset.owning_entity == owning_entity))
    return list(result.scalars().all())


async def list_active_licenses_for_asset(db: AsyncSession, *, tenant_id: str, ip_asset_id: uuid.UUID) -> list[LicenseAgreement]:
    result = await db.execute(
        select(LicenseAgreement).where(LicenseAgreement.tenant_id == tenant_id, LicenseAgreement.ip_asset_id == ip_asset_id, LicenseAgreement.status == LicenseStatus.ACTIVE.value)
    )
    return list(result.scalars().all())
