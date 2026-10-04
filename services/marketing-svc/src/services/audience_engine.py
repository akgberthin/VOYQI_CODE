"""Audience segmentation engine. Fully autonomous — defining and
recalculating a segment's membership is not an access-control
decision. This platform's member/CRM data source is stubbed as a
count for now (see recalculate_segment's docstring); a real deployment
wires this to subscription-svc/crm-svc's actual member records."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AudienceSegment


async def create_segment(db: AsyncSession, tenant_id: str, name: str, filter_criteria: dict, created_by: str) -> AudienceSegment:
    segment = AudienceSegment(tenant_id=tenant_id, name=name, filter_criteria=filter_criteria, created_by=created_by)
    db.add(segment)
    await db.commit()
    await db.refresh(segment)
    return segment


async def get_segment(db: AsyncSession, tenant_id: str, segment_id: uuid.UUID) -> AudienceSegment | None:
    result = await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id, AudienceSegment.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_segments(db: AsyncSession, tenant_id: str) -> list[AudienceSegment]:
    result = await db.execute(select(AudienceSegment).where(AudienceSegment.tenant_id == tenant_id))
    return list(result.scalars().all())


async def set_member_count(db: AsyncSession, tenant_id: str, segment_id: uuid.UUID, member_count: int) -> AudienceSegment | None:
    """Fully autonomous: a real deployment calls this after querying
    subscription-svc/crm-svc for the actual member records matching
    filter_criteria — that cross-service query is outside this
    service's own data, so it's supplied by the caller rather than
    computed here, the same pattern as corporate_engine's utilization
    scan taking trip counts as an argument rather than owning trip
    data it doesn't have."""
    segment = await get_segment(db, tenant_id, segment_id)
    if segment is None:
        return None
    segment.member_count = member_count
    await db.commit()
    await db.refresh(segment)
    return segment
