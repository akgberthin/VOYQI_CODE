"""
MOB-008 Global Mobility Continuity Console engine.

declare_travel resolves the destination-market equivalent class (via
plan_engine's class-equivalency rules), searches real fleet-svc
availability, and stages a vehicle by proposing a transfer — same
PROPOSED-not-dispatched contract as everywhere else a vehicle move
originates.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import MobilityClass, TravelDeclaration
from src.services import fleet_tools, plan_engine


async def declare_travel(
    db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID, source_class: MobilityClass,
    destination_market: str, destination_hub_id: str,
) -> TravelDeclaration:
    equivalent_class = await plan_engine.resolve_equivalent_class(db, tenant_id, source_class, destination_market)

    declaration = TravelDeclaration(
        tenant_id=tenant_id, member_profile_id=member_profile_id, destination_market=destination_market,
        resolved_equivalent_class=equivalent_class,
    )
    db.add(declaration)

    available = fleet_tools.search_available_vehicles(tenant_id, equivalent_class.value, hub_id=destination_hub_id)
    if available:
        vehicle = available[0]
        fleet_tools.propose_transfer(
            tenant_id, vehicle["id"], destination_hub_id, f"travel continuity for member {member_profile_id}"
        )
        declaration.staged_vehicle_id = uuid.UUID(vehicle["id"])
        declaration.staged_hub_id = uuid.UUID(destination_hub_id)

    await db.commit()
    await db.refresh(declaration)
    return declaration


async def complete_travel(db: AsyncSession, tenant_id: str, declaration_id: uuid.UUID) -> TravelDeclaration | None:
    result = await db.execute(select(TravelDeclaration).where(TravelDeclaration.id == declaration_id, TravelDeclaration.tenant_id == tenant_id))
    declaration = result.scalar_one_or_none()
    if declaration is None or not declaration.is_active:
        return None
    declaration.is_active = False
    declaration.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(declaration)
    return declaration


async def list_active_declarations(db: AsyncSession, tenant_id: str, member_profile_id: uuid.UUID) -> list[TravelDeclaration]:
    result = await db.execute(
        select(TravelDeclaration).where(
            TravelDeclaration.tenant_id == tenant_id, TravelDeclaration.member_profile_id == member_profile_id,
            TravelDeclaration.is_active.is_(True),
        )
    )
    return list(result.scalars().all())
