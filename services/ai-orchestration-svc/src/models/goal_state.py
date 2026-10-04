"""
GoalState — AI-005 AI Goal & Threshold Management.

Operator-defined targets each agent optimizes toward. Changing a goal
recalibrates agent behavior immediately (the agent reads current
GoalState rows every time it reasons, never a cached/compiled config),
and every change is retained as history via GoalStateHistory rather than
overwritten in place.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class GoalState(Base):
    __tablename__ = "goal_states"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    agent_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    goal_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    extra: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class GoalStateHistory(Base):
    __tablename__ = "goal_state_history"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    goal_state_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("goal_states.id"), nullable=False, index=True
    )

    previous_target_value: Mapped[float] = mapped_column(Float, nullable=False)
    new_target_value: Mapped[float] = mapped_column(Float, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
