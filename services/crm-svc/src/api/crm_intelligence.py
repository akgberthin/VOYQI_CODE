"""Identity resolution and inbox endpoints — the contract
ai-orchestration-svc's crm_tools calls, plus the human actions that
resolve what the agents propose."""
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.kafka_events import publish_event
from src.core.security import get_tenant_id
from src.services import crm_intelligence_engine as engine

router = APIRouter()


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PersonProfileIn(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None


class PersonProfileOut(_Out):
    id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    merged_into_id: uuid.UUID | None
    created_at: datetime | None


class SourceRecordIn(BaseModel):
    source_system: str
    full_name: str
    email: str | None = None
    phone: str | None = None


class SourceRecordOut(_Out):
    id: uuid.UUID
    source_system: str
    full_name: str
    email: str | None
    phone: str | None
    resolved_profile_id: uuid.UUID | None


class MatchCandidateIn(BaseModel):
    source_record_id: uuid.UUID
    candidate_person_profile_id: uuid.UUID | None = None
    match_score: float = Field(ge=0.0, le=1.0)
    match_reasoning: str


class MatchCandidateOut(_Out):
    id: uuid.UUID
    source_record_id: uuid.UUID
    candidate_person_profile_id: uuid.UUID | None
    match_score: float
    match_reasoning: str
    status: str
    resolved_by: str | None


class ProfileDuplicateIn(BaseModel):
    profile_a_id: uuid.UUID
    profile_b_id: uuid.UUID
    match_score: float = Field(ge=0.0, le=1.0)
    match_reasoning: str


class ProfileDuplicateOut(_Out):
    id: uuid.UUID
    profile_a_id: uuid.UUID
    profile_b_id: uuid.UUID
    match_score: float
    match_reasoning: str
    status: str
    resolved_by: str | None


class ResolveIn(BaseModel):
    confirm: bool
    resolved_by: str


class ConversationIn(BaseModel):
    channel: str = "email"
    subject: str = ""
    person_profile_id: uuid.UUID | None = None


class ConversationOut(_Out):
    id: uuid.UUID
    person_profile_id: uuid.UUID | None
    channel: str
    subject: str
    status: str
    priority: str
    routed_to_team: str | None


class MessageIn(BaseModel):
    body: str = Field(min_length=1)
    sender: str | None = None


class MessageOut(_Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    direction: str
    body: str
    sender: str | None
    sentiment: str | None
    intent: str | None
    created_at: datetime | None


class SentimentIntentIn(BaseModel):
    sentiment: str
    intent: str


class DraftReplyIn(BaseModel):
    conversation_id: uuid.UUID
    in_reply_to_message_id: uuid.UUID | None = None
    draft_body: str
    reasoning: str = ""


class DraftReplyOut(_Out):
    id: uuid.UUID
    conversation_id: uuid.UUID
    in_reply_to_message_id: uuid.UUID | None
    draft_body: str
    reasoning: str
    status: str
    approved_by: str | None
    sent_message_id: uuid.UUID | None


class ApproveDraftIn(BaseModel):
    approved_by: str
    edited_body: str | None = None


class DiscardDraftIn(BaseModel):
    discarded_by: str


class EscalateIn(BaseModel):
    priority: str
    routed_to_team: str


def _not_found(exc: Exception):
    return HTTPException(status_code=404, detail=str(exc))


def _profile_created(background_tasks: BackgroundTasks, tenant_id: str, profile) -> None:
    background_tasks.add_task(
        publish_event, "crm.identity", "crm.person_profile_created", tenant_id,
        {"person_profile_id": str(profile.id), "full_name": profile.full_name, "email": profile.email or "", "phone": profile.phone or ""},
    )


# --- identity --------------------------------------------------------------


@router.post("/person-profiles", response_model=PersonProfileOut, status_code=201)
async def create_person_profile(body: PersonProfileIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Routes to the Revenue Agent's duplicate check (CRM-007)."""
    profile = await engine.create_person_profile(db, tenant_id, body.full_name, body.email, body.phone)
    await db.commit()
    _profile_created(background_tasks, tenant_id, profile)
    return profile


@router.get("/person-profiles", response_model=list[PersonProfileOut])
async def list_person_profiles(include_merged: bool = False, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_person_profiles(db, tenant_id, include_merged)


@router.post("/source-records", response_model=SourceRecordOut, status_code=201)
async def create_source_record(body: SourceRecordIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Routes to the Revenue Agent's identity resolution (CRM-001)."""
    record = await engine.create_source_record(db, tenant_id, body.source_system, body.full_name, body.email, body.phone)
    await db.commit()
    background_tasks.add_task(
        publish_event, "crm.identity", "crm.source_record_created", tenant_id,
        {"source_record_id": str(record.id), "source_system": record.source_system, "full_name": record.full_name, "email": record.email or "", "phone": record.phone or ""},
    )
    return record


@router.post("/match-candidates", response_model=MatchCandidateOut, status_code=201)
async def create_match_candidate(body: MatchCandidateIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        candidate = await engine.create_match_candidate(
            db, tenant_id, body.source_record_id, body.candidate_person_profile_id, body.match_score, body.match_reasoning
        )
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    await db.commit()
    return candidate


@router.get("/match-candidates", response_model=list[MatchCandidateOut])
async def list_match_candidates(status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_match_candidates(db, tenant_id, status)


@router.post("/match-candidates/{candidate_id}/resolve", response_model=MatchCandidateOut)
async def resolve_match_candidate(candidate_id: uuid.UUID, body: ResolveIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        candidate, new_profile = await engine.resolve_match_candidate(db, tenant_id, candidate_id, body.confirm, body.resolved_by)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    except engine.AlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    if new_profile is not None:
        _profile_created(background_tasks, tenant_id, new_profile)
    return candidate


@router.post("/profile-duplicate-candidates", response_model=ProfileDuplicateOut, status_code=201)
async def create_profile_duplicate_candidate(body: ProfileDuplicateIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        candidate = await engine.create_profile_duplicate_candidate(
            db, tenant_id, body.profile_a_id, body.profile_b_id, body.match_score, body.match_reasoning
        )
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return candidate


@router.get("/profile-duplicate-candidates", response_model=list[ProfileDuplicateOut])
async def list_profile_duplicate_candidates(status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_profile_duplicate_candidates(db, tenant_id, status)


@router.post("/profile-duplicate-candidates/{candidate_id}/resolve", response_model=ProfileDuplicateOut)
async def resolve_profile_duplicate(candidate_id: uuid.UUID, body: ResolveIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        candidate = await engine.resolve_profile_duplicate(db, tenant_id, candidate_id, body.confirm, body.resolved_by)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    except engine.AlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return candidate


# --- inbox -----------------------------------------------------------------


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(body: ConversationIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        conversation = await engine.create_conversation(db, tenant_id, body.channel, body.subject, body.person_profile_id)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    await db.commit()
    return conversation


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_conversations(db, tenant_id, status)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await engine.get_conversation(db, tenant_id, conversation_id)
    except engine.NotFoundError as exc:
        raise _not_found(exc)


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def receive_message(conversation_id: uuid.UUID, body: MessageIn, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """An inbound customer message — routes to the Concierge Agent (CRM-003)."""
    try:
        message = await engine.receive_message(db, tenant_id, conversation_id, body.body, body.sender)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    await db.commit()
    background_tasks.add_task(
        publish_event, "crm.inbox", "crm.message_received", tenant_id,
        {"message_id": str(message.id), "conversation_id": str(conversation_id), "body": message.body},
    )
    return message


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(conversation_id: uuid.UUID, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await engine.list_messages(db, tenant_id, conversation_id)
    except engine.NotFoundError as exc:
        raise _not_found(exc)


@router.post("/messages/{message_id}/sentiment-intent", response_model=MessageOut)
async def set_sentiment_intent(message_id: uuid.UUID, body: SentimentIntentIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        message = await engine.set_sentiment_intent(db, tenant_id, message_id, body.sentiment, body.intent)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    await db.commit()
    return message


@router.post("/draft-replies", response_model=DraftReplyOut, status_code=201)
async def create_draft_reply(body: DraftReplyIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        draft = await engine.create_draft_reply(db, tenant_id, body.conversation_id, body.in_reply_to_message_id, body.draft_body, body.reasoning)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    await db.commit()
    return draft


@router.get("/draft-replies", response_model=list[DraftReplyOut])
async def list_draft_replies(status: str | None = None, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await engine.list_draft_replies(db, tenant_id, status)


@router.post("/draft-replies/{draft_id}/approve-and-send", response_model=DraftReplyOut)
async def approve_and_send_draft(draft_id: uuid.UUID, body: ApproveDraftIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """A human sends the reply — the agent only ever drafts it."""
    try:
        draft, _message = await engine.approve_and_send_draft(db, tenant_id, draft_id, body.approved_by, body.edited_body)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    except engine.AlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return draft


@router.post("/draft-replies/{draft_id}/discard", response_model=DraftReplyOut)
async def discard_draft(draft_id: uuid.UUID, body: DiscardDraftIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        draft = await engine.discard_draft(db, tenant_id, draft_id, body.discarded_by)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    except engine.AlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return draft


@router.post("/conversations/{conversation_id}/escalate", response_model=ConversationOut)
async def escalate_conversation(conversation_id: uuid.UUID, body: EscalateIn, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        conversation = await engine.escalate_conversation(db, tenant_id, conversation_id, body.priority, body.routed_to_team)
    except engine.NotFoundError as exc:
        raise _not_found(exc)
    await db.commit()
    return conversation
