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


# ── Rule-based audiences over the tenant's contacts ─────────────────────
# A contact is anyone in any segment (one person, matched by email or
# phone, however many segments they're in). Criteria are
# {field: rule}: a list matches any value, {"min": a, "max": b} a numeric
# range, "channel_opt_in" a channel the contact can be reached on, and
# anything else equals the attribute (case-insensitive).

REACH = {"email": lambda m: bool(m["email"]), "sms": lambda m: bool(m["phone"]), "push": lambda m: bool(m["push_token"]),
         "voice": lambda m: bool(m["phone"]) and m["consent_voice"]}


class InvalidCriteria(ValueError):
    pass


async def contacts(db: AsyncSession, tenant_id: str) -> list[dict]:
    from src.models import SegmentMember

    people: dict[str, dict] = {}
    for m in (await db.execute(select(SegmentMember).where(SegmentMember.tenant_id == tenant_id))).scalars():
        key = (m.email or "").lower() or m.phone or m.push_token
        p = people.setdefault(key, {"name": "", "email": "", "phone": "", "push_token": "", "consent_voice": False, "attributes": {}})
        for f in ("name", "email", "phone", "push_token"):
            p[f] = p[f] or getattr(m, f) or ""
        p["consent_voice"] = p["consent_voice"] or bool(m.consent_voice)
        p["attributes"] = {**p["attributes"], **(m.attributes or {})}
    return list(people.values())


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def matches(contact: dict, criteria: dict) -> bool:
    attrs = contact["attributes"]
    for field, rule in (criteria or {}).items():
        if rule in (None, "", []):
            continue
        if field == "channel_opt_in":
            if not all(REACH[c](contact) for c in ([rule] if isinstance(rule, str) else rule)):
                return False
            continue
        value = attrs.get(field)
        if isinstance(rule, dict):
            n = _num(value)
            if n is None or ("min" in rule and n < float(rule["min"])) or ("max" in rule and n > float(rule["max"])):
                return False
        elif isinstance(rule, list):
            if str(value or "").lower() not in {str(r).lower() for r in rule}:
                return False
        elif str(value or "").lower() != str(rule).lower():
            return False
    return True


def check_criteria(criteria: dict) -> dict:
    if not isinstance(criteria, dict):
        raise InvalidCriteria("filter_criteria must be an object")
    for field, rule in criteria.items():
        if field == "channel_opt_in":
            for c in [rule] if isinstance(rule, str) else rule or []:
                if c not in REACH:
                    raise InvalidCriteria(f"channel_opt_in must be one of {', '.join(REACH)}")
        elif isinstance(rule, dict):
            if not set(rule) <= {"min", "max"} or any(_num(v) is None for v in rule.values()):
                raise InvalidCriteria(f"{field}: a range is {{\"min\": number, \"max\": number}}")
    return criteria


async def estimate(db: AsyncSession, tenant_id: str, criteria: dict) -> dict:
    check_criteria(criteria)
    everyone = await contacts(db, tenant_id)
    hits = [c for c in everyone if matches(c, criteria)]
    return {"total": len(hits), "of": len(everyone), "reachable": {ch: sum(1 for c in hits if ok(c)) for ch, ok in REACH.items()}}


async def attribute_values(db: AsyncSession, tenant_id: str) -> dict[str, list]:
    """Every attribute contacts have, with its values (up to 50) — what the
    audience builder offers as filters."""
    seen: dict[str, set] = {}
    for c in await contacts(db, tenant_id):
        for k, v in c["attributes"].items():
            if v not in (None, ""):
                seen.setdefault(k, set()).add(str(v))
    return {k: sorted(v, key=lambda x: (_num(x) is None, _num(x) or 0, x))[:50] for k, v in sorted(seen.items())}


async def populate(db: AsyncSession, tenant_id: str, segment_id: uuid.UUID, criteria: dict) -> int:
    """Copies every contact matching the criteria into the segment."""
    hits = [c for c in await contacts(db, tenant_id) if matches(c, criteria)]
    return await add_members(db, tenant_id, segment_id, hits)


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
        attrs = {str(k).strip(): v for k, v in (m.get("attributes") or {}).items() if str(k).strip() and v not in (None, "")}
        db.add(SegmentMember(tenant_id=tenant_id, segment_id=segment_id, name=(m.get("name") or "").strip(), email=email, phone=phone,
                             push_token=(m.get("push_token") or "").strip(), consent_voice=bool(m.get("consent_voice")), attributes=attrs))
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
