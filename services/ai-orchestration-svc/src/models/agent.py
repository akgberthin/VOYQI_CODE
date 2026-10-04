"""
Agent registry — AI-001 Agent Operations Console.

Every specialist agent (Compliance/Audit, Pricing Intelligence, Fleet
Intelligence, CRM Intelligence, HRM Talent, etc.) is a row here, not a
separate service. One orchestration service, one shared context, many
specializations — per the platform's standing architectural rule that
agents must never be built as independent services that cannot share
context.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus), nullable=False, default=AgentStatus.ACTIVE
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="claude-sonnet-5")
    model_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Human-approval threshold: recommendations at or above this risk
    # score require a human decision before the agent's action executes.
    human_approval_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    decisions_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommendations_pending: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
