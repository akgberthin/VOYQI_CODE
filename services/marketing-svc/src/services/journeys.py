"""Automation journeys: multi-step, multi-channel sequences that start when
a platform event happens for a contact (or when the contact is enrolled
by hand) and send each step after its delay. Every step goes through the
same suppression list, opt-out preferences, voice consent and calling
window as any campaign send."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Journey, JourneyEnrollment, SegmentMember
from src.services import channel_policy, delivery, suppression_engine
from src.services.send_engine import CONTACT_FIELD, personalize

CHANNELS = ("email", "sms", "push", "voice")

# The prototype's flows, as starting points.
TEMPLATES = [
    {"key": "welcome", "name": "Welcome sequence", "trigger_event": "user.created", "steps": [
        {"delay_hours": 0, "channel": "email", "subject": "Welcome to VOYQI, {first_name}", "body": "Hi {first_name}, your membership is ready. Pick up at any VOYQI hub — the keys are in the app."},
        {"delay_hours": 24, "channel": "push", "subject": "Your nearest hub", "body": "Open the hub map to see vehicles staged near you."},
        {"delay_hours": 72, "channel": "sms", "subject": "", "body": "VOYQI: ready for your first trip, {first_name}? Reserve in the app. Reply STOP to opt out."},
        {"delay_hours": 168, "channel": "email", "subject": "Getting the most from GU", "body": "Hi {first_name}, here's how Global Units (GU) work and how to earn more with every trip."},
    ]},
    {"key": "lead_nurture", "name": "New lead nurture", "trigger_event": "lead.captured", "steps": [
        {"delay_hours": 0, "channel": "email", "subject": "Thanks for your interest, {first_name}", "body": "Hi {first_name}, thanks for reaching out. Here's how Portable Mobility works: one membership, every class, every hub."},
        {"delay_hours": 48, "channel": "sms", "subject": "", "body": "VOYQI: questions about membership, {first_name}? Reply and a concierge will help. Reply STOP to opt out."},
    ]},
    {"key": "winback", "name": "Winback sequence", "trigger_event": "subscription.cancelled", "steps": [
        {"delay_hours": 720, "channel": "email", "subject": "We miss you, {first_name}", "body": "Hi {first_name}, your favourite class is still waiting. Come back this month and your first week is on us."},
        {"delay_hours": 1440, "channel": "sms", "subject": "", "body": "VOYQI: a returning-member offer is waiting for you, {first_name}. Reply STOP to opt out."},
        {"delay_hours": 2160, "channel": "voice", "subject": "", "body": "Hello {first_name}, we'd love to welcome you back to VOYQI with a returning-member offer. Open the app to see it."},
    ]},
    {"key": "churn_prevention", "name": "Churn prevention", "trigger_event": "manual", "steps": [
        {"delay_hours": 0, "channel": "email", "subject": "Still with us, {first_name}?", "body": "Hi {first_name}, we noticed you haven't driven in a while. Here's what's new at your nearest hub."},
        {"delay_hours": 72, "channel": "sms", "subject": "", "body": "VOYQI: need a break? You can pause your membership any time in the app. Reply STOP to opt out."},
    ]},
]


class JourneyError(Exception):
    pass


def validate_steps(steps: list[dict]) -> list[dict]:
    if not steps:
        raise JourneyError("a journey needs at least one step")
    clean = []
    for i, st in enumerate(steps):
        channel = st.get("channel")
        if channel not in CHANNELS:
            raise JourneyError(f"step {i + 1}: channel must be one of {', '.join(CHANNELS)}")
        if not str(st.get("body", "")).strip():
            raise JourneyError(f"step {i + 1}: the message is empty")
        delay = int(st.get("delay_hours", 0))
        if delay < 0:
            raise JourneyError(f"step {i + 1}: delay can't be negative")
        clean.append({"delay_hours": delay, "channel": channel, "subject": str(st.get("subject", "")), "body": str(st["body"])})
    return clean


async def create(db: AsyncSession, tenant_id: str, name: str, trigger_event: str, steps: list[dict], actor: str) -> Journey:
    journey = Journey(tenant_id=tenant_id, name=name, trigger_event=trigger_event or "manual", status="paused", steps=validate_steps(steps), created_by=actor)
    db.add(journey)
    await db.commit()
    await db.refresh(journey)
    return journey


async def get(db: AsyncSession, tenant_id: str, journey_id: uuid.UUID) -> Journey:
    journey = (await db.execute(select(Journey).where(Journey.id == journey_id, Journey.tenant_id == tenant_id))).scalar_one_or_none()
    if journey is None:
        raise JourneyError("journey not found")
    return journey


async def update_journey(db: AsyncSession, tenant_id: str, journey_id: uuid.UUID, values: dict) -> Journey:
    journey = await get(db, tenant_id, journey_id)
    if "steps" in values:
        journey.steps = validate_steps(values["steps"])
    if "name" in values:
        journey.name = values["name"]
    if "trigger_event" in values:
        journey.trigger_event = values["trigger_event"] or "manual"
    if "status" in values:
        if values["status"] not in ("active", "paused"):
            raise JourneyError("status must be active or paused")
        journey.status = values["status"]
    await db.commit()
    await db.refresh(journey)
    return journey


async def list_journeys(db: AsyncSession, tenant_id: str) -> list[dict]:
    journeys = list((await db.execute(select(Journey).where(Journey.tenant_id == tenant_id).order_by(Journey.created_at))).scalars())
    counts = {}
    for jid, status, n in (await db.execute(
        select(JourneyEnrollment.journey_id, JourneyEnrollment.status, func.count()).where(JourneyEnrollment.tenant_id == tenant_id)
        .group_by(JourneyEnrollment.journey_id, JourneyEnrollment.status)
    )).all():
        counts.setdefault(jid, {})[status] = n
    return [{"journey": j, "enrollments": counts.get(j.id, {})} for j in journeys]


async def enroll(db: AsyncSession, tenant_id: str, journey: Journey, contact: dict, source: str, now: datetime | None = None) -> JourneyEnrollment | None:
    """Starts the journey for a contact, unless they're already in it."""
    now = now or datetime.now(timezone.utc)
    email, phone = (contact.get("email") or "").strip().lower(), (contact.get("phone") or "").strip()
    if not (email or phone or contact.get("push_token")):
        return None
    clauses = [JourneyEnrollment.journey_id == journey.id, JourneyEnrollment.status == "active"]
    if email:
        clauses.append(JourneyEnrollment.email == email)
    else:
        clauses.append(JourneyEnrollment.phone == phone)
    if (await db.execute(select(JourneyEnrollment.id).where(*clauses))).first():
        return None
    first = journey.steps[0]
    row = JourneyEnrollment(
        tenant_id=tenant_id, journey_id=journey.id, name=(contact.get("name") or contact.get("full_name") or "").strip(),
        email=email, phone=phone, push_token=(contact.get("push_token") or "").strip(), consent_voice=bool(contact.get("consent_voice")),
        current_step=0, next_run_at=now + timedelta(hours=first["delay_hours"]), status="active", history=[], source=source,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def enroll_segment(db: AsyncSession, tenant_id: str, journey_id: uuid.UUID, segment_id: uuid.UUID) -> int:
    journey = await get(db, tenant_id, journey_id)
    members = (await db.execute(select(SegmentMember).where(SegmentMember.segment_id == segment_id, SegmentMember.tenant_id == tenant_id))).scalars().all()
    added = 0
    for m in members:
        if await enroll(db, tenant_id, journey, {"name": m.name, "email": m.email, "phone": m.phone, "push_token": m.push_token, "consent_voice": m.consent_voice}, f"segment:{segment_id}"):
            added += 1
    return added


async def on_event(db: AsyncSession, tenant_id: str, event_type: str, payload: dict) -> int:
    """Enrolls the event's contact in every active journey it triggers."""
    journeys = (await db.execute(select(Journey).where(Journey.tenant_id == tenant_id, Journey.status == "active", Journey.trigger_event == event_type))).scalars().all()
    started = 0
    for journey in journeys:
        if await enroll(db, tenant_id, journey, payload, f"event:{event_type}"):
            started += 1
    return started


async def run_step(db: AsyncSession, enrollment: JourneyEnrollment, now: datetime | None = None) -> None:
    """Sends the enrollment's current step and books the next one."""
    now = now or datetime.now(timezone.utc)
    journey = (await db.execute(select(Journey).where(Journey.id == enrollment.journey_id))).scalar_one()
    if journey.status != "active":
        enrollment.claimed_at = None  # paused: try again later
        enrollment.next_run_at = now + timedelta(hours=1)
        await db.commit()
        return
    step = journey.steps[enrollment.current_step]
    channel = step["channel"]
    contact = getattr(enrollment, CONTACT_FIELD[channel])
    outcome = None
    if channel == "voice":
        policy = await channel_policy.get(db, enrollment.tenant_id)
        if not channel_policy.in_voice_window(policy, now):
            enrollment.next_run_at, enrollment.claimed_at = channel_policy.next_window_start(policy, now), None
            await db.commit()
            return
        if not enrollment.consent_voice:
            outcome = "skipped: no voice consent"
    if outcome is None and not contact:
        outcome = f"skipped: no {CONTACT_FIELD[channel].replace('_', ' ')}"
    if outcome is None and await suppression_engine.is_suppressed(db, enrollment.tenant_id, channel, contact):
        outcome = "skipped: suppressed"
    if outcome is None:
        result = await delivery.deliver(enrollment.tenant_id, channel, contact, personalize(step["body"], enrollment.name),
                                        personalize(step["subject"], enrollment.name), f"journey:{journey.id}")
        outcome = result.get("status", "failed") + (f" ({result.get('delivery_mode')})" if result.get("delivery_mode") else "") + (f": {result['error_message']}" if result.get("error_message") else "")
    enrollment.history = [*enrollment.history, {"step": enrollment.current_step + 1, "channel": channel, "at": now.isoformat(), "outcome": outcome}]
    enrollment.current_step += 1
    enrollment.claimed_at = None
    if enrollment.current_step >= len(journey.steps):
        enrollment.status, enrollment.next_run_at = "completed", None
    else:
        nxt = journey.steps[enrollment.current_step]
        enrollment.next_run_at = now + timedelta(hours=max(nxt["delay_hours"] - step["delay_hours"], 0))
    await db.commit()


async def due(db: AsyncSession, now: datetime | None = None) -> list[uuid.UUID]:
    now = now or datetime.now(timezone.utc)
    result = await db.execute(select(JourneyEnrollment.id).where(JourneyEnrollment.status == "active", JourneyEnrollment.claimed_at.is_(None), JourneyEnrollment.next_run_at <= now))
    return [r[0] for r in result.all()]


async def claim(db: AsyncSession, enrollment_id: uuid.UUID) -> JourneyEnrollment | None:
    result = await db.execute(update(JourneyEnrollment).where(JourneyEnrollment.id == enrollment_id, JourneyEnrollment.claimed_at.is_(None), JourneyEnrollment.status == "active")
                              .values(claimed_at=datetime.now(timezone.utc)))
    await db.commit()
    if result.rowcount != 1:
        return None
    return (await db.execute(select(JourneyEnrollment).where(JourneyEnrollment.id == enrollment_id))).scalar_one()


async def stop(db: AsyncSession, tenant_id: str, enrollment_id: uuid.UUID) -> JourneyEnrollment:
    row = (await db.execute(select(JourneyEnrollment).where(JourneyEnrollment.id == enrollment_id, JourneyEnrollment.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise JourneyError("enrollment not found")
    row.status, row.next_run_at = "stopped", None
    await db.commit()
    await db.refresh(row)
    return row


async def enrollments(db: AsyncSession, tenant_id: str, journey_id: uuid.UUID) -> list[JourneyEnrollment]:
    result = await db.execute(select(JourneyEnrollment).where(JourneyEnrollment.tenant_id == tenant_id, JourneyEnrollment.journey_id == journey_id).order_by(JourneyEnrollment.enrolled_at.desc()))
    return list(result.scalars().all())
