"""Enterprise CRM intelligence: identity resolution records and the
conversation inbox.

The Revenue and Concierge Agents in ai-orchestration-svc only propose —
match candidates, duplicate candidates, sentiment/intent tags, draft
replies, escalations. Merging two identities and sending a reply to a
customer are human actions (resolve_* and approve_and_send_draft).
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    CandidateStatus,
    Conversation,
    ConversationStatus,
    DraftReply,
    DraftReplyStatus,
    MatchCandidate,
    Message,
    MessageDirection,
    PersonProfile,
    ProfileDuplicateCandidate,
    SourceRecord,
)


class NotFoundError(Exception):
    pass


class AlreadyResolvedError(Exception):
    pass


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def _get(db: AsyncSession, model, tenant_id: str, record_id: uuid.UUID):
    result = await db.execute(select(model).where(model.id == record_id, model.tenant_id == tenant_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"{model.__tablename__} {record_id} not found")
    return record


# --- identity --------------------------------------------------------------


async def create_person_profile(db: AsyncSession, tenant_id: str, full_name: str, email: str | None, phone: str | None) -> PersonProfile:
    profile = PersonProfile(tenant_id=tenant_id, full_name=full_name.strip(), email=_clean(email) and email.lower().strip(), phone=_clean(phone))
    db.add(profile)
    await db.flush()
    return profile


async def list_person_profiles(db: AsyncSession, tenant_id: str, include_merged: bool = False) -> list[PersonProfile]:
    query = select(PersonProfile).where(PersonProfile.tenant_id == tenant_id)
    if not include_merged:
        query = query.where(PersonProfile.merged_into_id.is_(None))
    result = await db.execute(query.order_by(PersonProfile.created_at))
    return list(result.scalars().all())


async def create_source_record(db: AsyncSession, tenant_id: str, source_system: str, full_name: str, email: str | None, phone: str | None) -> SourceRecord:
    record = SourceRecord(
        tenant_id=tenant_id, source_system=source_system, full_name=full_name.strip(),
        email=_clean(email) and email.lower().strip(), phone=_clean(phone),
    )
    db.add(record)
    await db.flush()
    return record


async def create_match_candidate(
    db: AsyncSession, tenant_id: str, source_record_id: uuid.UUID, candidate_person_profile_id: uuid.UUID | None,
    match_score: float, match_reasoning: str,
) -> MatchCandidate:
    await _get(db, SourceRecord, tenant_id, source_record_id)
    if candidate_person_profile_id is not None:
        await _get(db, PersonProfile, tenant_id, candidate_person_profile_id)
    candidate = MatchCandidate(
        tenant_id=tenant_id, source_record_id=source_record_id, candidate_person_profile_id=candidate_person_profile_id,
        match_score=match_score, match_reasoning=match_reasoning,
    )
    db.add(candidate)
    await db.flush()
    return candidate


async def list_match_candidates(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[MatchCandidate]:
    query = select(MatchCandidate).where(MatchCandidate.tenant_id == tenant_id)
    if status:
        query = query.where(MatchCandidate.status == status)
    result = await db.execute(query.order_by(MatchCandidate.created_at))
    return list(result.scalars().all())


async def resolve_match_candidate(
    db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID, confirm: bool, resolved_by: str,
) -> tuple[MatchCandidate, PersonProfile | None]:
    """Confirming links the source record to the candidate profile — or,
    when the agent found no match, to a newly created profile (returned
    so the caller can publish crm.person_profile_created)."""
    candidate = await _get(db, MatchCandidate, tenant_id, candidate_id)
    if candidate.status != CandidateStatus.PENDING.value:
        raise AlreadyResolvedError(f"match candidate {candidate_id} is already {candidate.status}")
    candidate.status = CandidateStatus.CONFIRMED.value if confirm else CandidateStatus.REJECTED.value
    candidate.resolved_by = resolved_by
    new_profile = None
    if confirm:
        record = await _get(db, SourceRecord, tenant_id, candidate.source_record_id)
        if candidate.candidate_person_profile_id is not None:
            record.resolved_profile_id = candidate.candidate_person_profile_id
        else:
            new_profile = await create_person_profile(db, tenant_id, record.full_name, record.email, record.phone)
            record.resolved_profile_id = new_profile.id
    await db.flush()
    return candidate, new_profile


async def create_profile_duplicate_candidate(
    db: AsyncSession, tenant_id: str, profile_a_id: uuid.UUID, profile_b_id: uuid.UUID, match_score: float, match_reasoning: str,
) -> ProfileDuplicateCandidate:
    if profile_a_id == profile_b_id:
        raise ValueError("a profile cannot be a duplicate of itself")
    await _get(db, PersonProfile, tenant_id, profile_a_id)
    await _get(db, PersonProfile, tenant_id, profile_b_id)
    candidate = ProfileDuplicateCandidate(
        tenant_id=tenant_id, profile_a_id=profile_a_id, profile_b_id=profile_b_id, match_score=match_score, match_reasoning=match_reasoning,
    )
    db.add(candidate)
    await db.flush()
    return candidate


async def list_profile_duplicate_candidates(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[ProfileDuplicateCandidate]:
    query = select(ProfileDuplicateCandidate).where(ProfileDuplicateCandidate.tenant_id == tenant_id)
    if status:
        query = query.where(ProfileDuplicateCandidate.status == status)
    result = await db.execute(query.order_by(ProfileDuplicateCandidate.created_at))
    return list(result.scalars().all())


async def resolve_profile_duplicate(
    db: AsyncSession, tenant_id: str, candidate_id: uuid.UUID, merge: bool, resolved_by: str,
) -> ProfileDuplicateCandidate:
    """Merging folds profile_b into profile_a — a false merge corrupts two
    people's histories, which is why only a human does this."""
    candidate = await _get(db, ProfileDuplicateCandidate, tenant_id, candidate_id)
    if candidate.status != CandidateStatus.PENDING.value:
        raise AlreadyResolvedError(f"duplicate candidate {candidate_id} is already {candidate.status}")
    candidate.status = CandidateStatus.CONFIRMED.value if merge else CandidateStatus.REJECTED.value
    candidate.resolved_by = resolved_by
    if merge:
        profile_b = await _get(db, PersonProfile, tenant_id, candidate.profile_b_id)
        profile_b.merged_into_id = candidate.profile_a_id
    await db.flush()
    return candidate


# --- inbox -----------------------------------------------------------------


async def create_conversation(
    db: AsyncSession, tenant_id: str, channel: str, subject: str, person_profile_id: uuid.UUID | None = None,
) -> Conversation:
    if person_profile_id is not None:
        await _get(db, PersonProfile, tenant_id, person_profile_id)
    conversation = Conversation(tenant_id=tenant_id, channel=channel, subject=subject, person_profile_id=person_profile_id)
    db.add(conversation)
    await db.flush()
    return conversation


async def get_conversation(db: AsyncSession, tenant_id: str, conversation_id: uuid.UUID) -> Conversation:
    return await _get(db, Conversation, tenant_id, conversation_id)


async def list_conversations(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[Conversation]:
    query = select(Conversation).where(Conversation.tenant_id == tenant_id)
    if status:
        query = query.where(Conversation.status == status)
    result = await db.execute(query.order_by(Conversation.created_at.desc()))
    return list(result.scalars().all())


async def receive_message(db: AsyncSession, tenant_id: str, conversation_id: uuid.UUID, body: str, sender: str | None) -> Message:
    conversation = await _get(db, Conversation, tenant_id, conversation_id)
    if conversation.status == ConversationStatus.CLOSED.value:
        conversation.status = ConversationStatus.OPEN.value
    message = Message(tenant_id=tenant_id, conversation_id=conversation_id, direction=MessageDirection.INBOUND.value, body=body, sender=sender)
    db.add(message)
    await db.flush()
    return message


async def list_messages(db: AsyncSession, tenant_id: str, conversation_id: uuid.UUID) -> list[Message]:
    await _get(db, Conversation, tenant_id, conversation_id)
    result = await db.execute(
        select(Message).where(Message.tenant_id == tenant_id, Message.conversation_id == conversation_id).order_by(Message.created_at)
    )
    return list(result.scalars().all())


async def set_sentiment_intent(db: AsyncSession, tenant_id: str, message_id: uuid.UUID, sentiment: str, intent: str) -> Message:
    message = await _get(db, Message, tenant_id, message_id)
    message.sentiment = sentiment
    message.intent = intent
    await db.flush()
    return message


async def create_draft_reply(
    db: AsyncSession, tenant_id: str, conversation_id: uuid.UUID, in_reply_to_message_id: uuid.UUID | None, draft_body: str, reasoning: str,
) -> DraftReply:
    await _get(db, Conversation, tenant_id, conversation_id)
    draft = DraftReply(
        tenant_id=tenant_id, conversation_id=conversation_id, in_reply_to_message_id=in_reply_to_message_id,
        draft_body=draft_body, reasoning=reasoning,
    )
    db.add(draft)
    await db.flush()
    return draft


async def list_draft_replies(db: AsyncSession, tenant_id: str, status: str | None = None) -> list[DraftReply]:
    query = select(DraftReply).where(DraftReply.tenant_id == tenant_id)
    if status:
        query = query.where(DraftReply.status == status)
    result = await db.execute(query.order_by(DraftReply.created_at))
    return list(result.scalars().all())


async def approve_and_send_draft(
    db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, approved_by: str, edited_body: str | None = None,
) -> tuple[DraftReply, Message]:
    draft = await _get(db, DraftReply, tenant_id, draft_id)
    if draft.status != DraftReplyStatus.PENDING.value:
        raise AlreadyResolvedError(f"draft reply {draft_id} is already {draft.status}")
    body = (edited_body or "").strip() or draft.draft_body
    message = Message(
        tenant_id=tenant_id, conversation_id=draft.conversation_id, direction=MessageDirection.OUTBOUND.value, body=body, sender=approved_by,
    )
    db.add(message)
    await db.flush()
    draft.status = DraftReplyStatus.SENT.value
    draft.approved_by = approved_by
    draft.sent_message_id = message.id
    await db.flush()
    return draft, message


async def discard_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, discarded_by: str) -> DraftReply:
    draft = await _get(db, DraftReply, tenant_id, draft_id)
    if draft.status != DraftReplyStatus.PENDING.value:
        raise AlreadyResolvedError(f"draft reply {draft_id} is already {draft.status}")
    draft.status = DraftReplyStatus.DISCARDED.value
    draft.approved_by = discarded_by
    await db.flush()
    return draft


async def escalate_conversation(db: AsyncSession, tenant_id: str, conversation_id: uuid.UUID, priority: str, routed_to_team: str) -> Conversation:
    conversation = await _get(db, Conversation, tenant_id, conversation_id)
    conversation.status = ConversationStatus.ESCALATED.value
    conversation.priority = priority
    conversation.routed_to_team = routed_to_team
    await db.flush()
    return conversation
