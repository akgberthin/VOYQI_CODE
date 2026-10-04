"""
Content draft engine — the deterministic guardrail floor every piece
of content passes through the instant it's created, independent of
whatever a live model was asked to draft.

This is the AI-native safety mechanism for this service: a draft that
passes the guardrail is auto-approved in the same call it's created,
no human click required. A draft that fails is held for a human to
look at, with the specific findings attached — the guardrail doesn't
silently reject, it explains what tripped it.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.models import ContentDraft, ContentStatus


def evaluate_guardrail(channel: str, subject_line: str, body_text: str) -> list[str]:
    """Pure, deterministic, and independently testable — the same
    function whether called from create_content_draft or a standalone
    re-check. Returns an empty list if the draft passes."""
    settings = get_settings()
    findings = []
    combined_lower = (subject_line + " " + body_text).lower()

    for phrase in settings.guardrail_max_claim_words:
        if phrase in combined_lower:
            findings.append(f'contains flagged claim language: "{phrase}"')

    if channel == "email" and len(subject_line) > settings.guardrail_email_subject_max_chars:
        findings.append(f"subject line is {len(subject_line)} chars, over the {settings.guardrail_email_subject_max_chars}-char limit")

    if channel == "sms" and len(body_text) > settings.guardrail_sms_max_chars:
        findings.append(f"body is {len(body_text)} chars, over the {settings.guardrail_sms_max_chars}-char SMS limit")

    if not body_text.strip():
        findings.append("body text is empty")

    return findings


async def create_content_draft(
    db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID, channel: str, body_text: str,
    created_by: str, subject_line: str = "", media_asset_ids: list[str] | None = None,
) -> ContentDraft:
    """Fully autonomous: the guardrail check and the resulting
    status — APPROVED or HELD_FOR_REVIEW — happen in the same call
    the draft is created in, not a separate step a caller has to
    remember to trigger."""
    findings = evaluate_guardrail(channel, subject_line, body_text)
    draft = ContentDraft(
        tenant_id=tenant_id, campaign_id=campaign_id, channel=channel, subject_line=subject_line,
        body_text=body_text, media_asset_ids=media_asset_ids or [], created_by=created_by,
        guardrail_findings=findings, status=ContentStatus.HELD_FOR_REVIEW if findings else ContentStatus.APPROVED,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def get_content_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID) -> ContentDraft | None:
    result = await db.execute(select(ContentDraft).where(ContentDraft.id == draft_id, ContentDraft.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def list_content_drafts(db: AsyncSession, tenant_id: str, campaign_id: uuid.UUID | None = None, status: str | None = None) -> list[ContentDraft]:
    query = select(ContentDraft).where(ContentDraft.tenant_id == tenant_id)
    if campaign_id:
        query = query.where(ContentDraft.campaign_id == campaign_id)
    if status:
        query = query.where(ContentDraft.status == status)
    result = await db.execute(query)
    return list(result.scalars().all())


async def human_approve_held_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, reviewed_by: str) -> ContentDraft | None:
    """The one human action this engine actually needs: clearing a
    draft that the guardrail held — not because a human always must
    approve, but because THIS SPECIFIC draft tripped a real finding."""
    draft = await get_content_draft(db, tenant_id, draft_id)
    if draft is None or draft.status != ContentStatus.HELD_FOR_REVIEW:
        return draft
    draft.status = ContentStatus.APPROVED
    draft.reviewed_by = reviewed_by
    await db.commit()
    await db.refresh(draft)
    return draft


async def reject_draft(db: AsyncSession, tenant_id: str, draft_id: uuid.UUID, reviewed_by: str) -> ContentDraft | None:
    draft = await get_content_draft(db, tenant_id, draft_id)
    if draft is None:
        return None
    draft.status = ContentStatus.REJECTED
    draft.reviewed_by = reviewed_by
    await db.commit()
    await db.refresh(draft)
    return draft
