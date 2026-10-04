import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ApplicationStatus, LoanApplication


async def create_application(
    db: AsyncSession, tenant_id: str, applicant_name: str, requested_amount_usd: float,
    annual_income_usd: float, fico_score: int,
) -> LoanApplication:
    application = LoanApplication(
        tenant_id=tenant_id, applicant_name=applicant_name, requested_amount_usd=requested_amount_usd,
        annual_income_usd=annual_income_usd, fico_score=fico_score,
    )
    db.add(application)
    await db.commit()
    await db.refresh(application)
    return application


async def get_application(db: AsyncSession, tenant_id: str, application_id: uuid.UUID) -> LoanApplication | None:
    result = await db.execute(
        select(LoanApplication).where(LoanApplication.tenant_id == tenant_id, LoanApplication.id == application_id)
    )
    return result.scalar_one_or_none()


async def list_applications(
    db: AsyncSession, tenant_id: str, status: ApplicationStatus | None = None
) -> list[LoanApplication]:
    stmt = select(LoanApplication).where(LoanApplication.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(LoanApplication.status == status)
    result = await db.execute(stmt.order_by(LoanApplication.created_at.desc()))
    return list(result.scalars().all())
