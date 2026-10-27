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


async def add_members(db: AsyncSession, tenant_id: str, segment_id: uuid.UUID, members: list[dict]) -> int:
    """Adds contacts, skipping ones already in the segment (same email or
    phone). Keeps member_count equal to the real number of contacts."""
    from src.models import AudienceSegment, SegmentMember

    segment = (await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id, AudienceSegment.tenant_id == tenant_id))).scalar_one_or_none()
    if segment is None:
        raise ValueError("segment not found")
    existing = (await db.execute(select(SegmentMember).where(SegmentMember.segment_id == segment_id))).scalars().all()
    seen = {m.email.lower() for m in existing if m.email} | {m.phone for m in existing if m.phone}
    added = 0
    for m in members:
        email, phone = (m.get("email") or "").strip().lower(), (m.get("phone") or "").strip()
        if not (email or phone or m.get("push_token")) or (email and email in seen) or (phone and phone in seen):
            continue
        db.add(SegmentMember(tenant_id=tenant_id, segment_id=segment_id, name=(m.get("name") or "").strip(), email=email, phone=phone, push_token=(m.get("push_token") or "").strip()))
        seen |= {x for x in (email, phone) if x}
        added += 1
    await db.flush()
    segment.member_count = len(existing) + added
    await db.commit()
    return added


async def list_members(db: AsyncSession, tenant_id: str, segment_id: uuid.UUID) -> list:
    from src.models import SegmentMember

    return list((await db.execute(select(SegmentMember).where(SegmentMember.segment_id == segment_id, SegmentMember.tenant_id == tenant_id))).scalars().all())


async def remove_member(db: AsyncSession, tenant_id: str, segment_id: uuid.UUID, member_id: uuid.UUID) -> bool:
    from src.models import AudienceSegment, SegmentMember

    member = (await db.execute(select(SegmentMember).where(SegmentMember.id == member_id, SegmentMember.segment_id == segment_id, SegmentMember.tenant_id == tenant_id))).scalar_one_or_none()
    if member is None:
        return False
    await db.delete(member)
    segment = (await db.execute(select(AudienceSegment).where(AudienceSegment.id == segment_id))).scalar_one()
    segment.member_count = max(segment.member_count - 1, 0)
    await db.commit()
    return True
