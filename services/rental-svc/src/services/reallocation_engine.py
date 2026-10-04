"""
Reallocation engine — the piece that makes this a dynamic operating
system rather than a set of independent per-hub calculations: it
looks across every hub for a given rental class and proposes moving a
vehicle from an underutilized hub to one under capacity risk.

Proposing a reallocation is fully autonomous (identifying the
opportunity requires no human judgment this engine doesn't already
have). Executing a physical vehicle transfer between hubs is a real
logistics operation with a cost and a lead time this service has no
visibility into — that action is deliberately left to the
administrator, the same way corporate_engine.resolve_utilization_flag
leaves seat reallocation to the corporate admin: this engine's job is
to surface the opportunity, not to move a physical asset on its own.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import RentalVehicle
from src.services import utilization_engine


@dataclass
class ReallocationSuggestion:
    rental_class: str
    donor_hub_id: str
    donor_utilization_pct: float
    recipient_hub_id: str
    recipient_utilization_pct: float
    utilization_gap_pct: float
    reasoning: str


async def find_reallocation_opportunities(db: AsyncSession, tenant_id: str, rental_class: str) -> list[ReallocationSuggestion]:
    """Autonomous cross-hub scan: every pair of hubs carrying this
    rental class is compared, and any pair whose utilization gap
    clears the configured threshold is surfaced as a real, specific
    suggestion — not a generic 'rebalance fleet' alert."""
    settings = get_settings()
    result = await db.execute(select(RentalVehicle.hub_id).where(RentalVehicle.tenant_id == tenant_id, RentalVehicle.rental_class == rental_class).distinct())
    hub_ids = [row[0] for row in result.all()]

    utilizations = {}
    for hub_id in hub_ids:
        utilizations[hub_id] = await utilization_engine.compute_utilization(db, tenant_id, hub_id, rental_class)

    suggestions = []
    for donor_id, donor in utilizations.items():
        for recipient_id, recipient in utilizations.items():
            if donor_id == recipient_id:
                continue
            gap = recipient["utilization_pct"] - donor["utilization_pct"]
            if gap >= settings.reallocation_min_utilization_gap_pct and donor["available_count"] > 0:
                suggestions.append(ReallocationSuggestion(
                    rental_class=rental_class, donor_hub_id=donor_id, donor_utilization_pct=donor["utilization_pct"],
                    recipient_hub_id=recipient_id, recipient_utilization_pct=recipient["utilization_pct"], utilization_gap_pct=round(gap, 1),
                    reasoning=(
                        f"Hub {donor_id} is at {donor['utilization_pct']}% utilization for {rental_class} with "
                        f"{donor['available_count']} vehicle(s) available, while hub {recipient_id} is at "
                        f"{recipient['utilization_pct']}% — a {round(gap, 1)}-point gap. Moving a vehicle would "
                        f"ease pressure at {recipient_id} without meaningfully reducing availability at {donor_id}."
                    ),
                ))
    return suggestions


async def execute_reallocation(db: AsyncSession, tenant_id: str, vehicle_id: uuid.UUID, new_hub_id: str) -> RentalVehicle | None:
    """The administrator's own action, taken after reviewing a
    suggestion — this records the physical move, it doesn't decide to
    make it."""
    result = await db.execute(select(RentalVehicle).where(RentalVehicle.id == vehicle_id, RentalVehicle.tenant_id == tenant_id))
    vehicle = result.scalar_one_or_none()
    if vehicle is None:
        return None
    vehicle.hub_id = new_hub_id
    await db.commit()
    await db.refresh(vehicle)
    return vehicle
