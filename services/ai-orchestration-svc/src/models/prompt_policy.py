"""
PromptPolicy — AI-003 Prompt & Policy Manager.

Version-controlled system prompts per agent. Only one version per agent
is active at a time; promoting a new version deactivates the prior one
but keeps it in the table (rollback is a promotion of an old version,
never a delete).
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class PolicyStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    RETIRED = "retired"


class PromptPolicy(Base):
    __tablename__ = "prompt_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    agent_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[PolicyStatus] = mapped_column(
        Enum(PolicyStatus), nullable=False, default=PolicyStatus.DRAFT
    )
    eval_suite_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    eval_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
