"""
DecisionLog — AI-004 AI Audit & Explainability Log.

An immutable record of every autonomous decision, written once and never
updated (overrides create a NEW log row that references the original,
they do not mutate history). This is what makes every AI-driven action
distinguishable from a human-driven one and gives compliance a full
input -> reasoning -> action -> outcome trace on demand.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    agent_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("ai_recommendations.id"), nullable=True, index=True
    )

    trigger_event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    tools_called: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    action_taken: Mapped[str] = mapped_column(String(256), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)  # success, failed, deferred

    # Autonomy tier this decision was evaluated under (1/2/3), per
    # docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md — nullable because
    # not every decision routes through the tier lookup yet; a decision
    # written before that wiring existed, or by a not-yet-migrated
    # agent, has no tier to report rather than a fabricated one.
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # True when a human overrode the agent — the row is new, additive,
    # and references the decision it supersedes rather than editing it.
    is_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supersedes_log_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("decision_logs.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


@event.listens_for(DecisionLog, "before_insert")
def _tag_autonomy_tier(mapper, connection, target: DecisionLog) -> None:
    """Fills tier/approval_required for any decision whose call site
    didn't set them, from the agent's tier-policy action
    (core/tiering.DECISION_ACTIONS). Human overrides are tagged by the
    approval path itself and are left alone."""
    if target.tier is not None or target.is_override:
        return
    from src.core.tiering import tier_for_decision  # deferred: tiering imports models

    tier = tier_for_decision(target.agent_key, target.trigger_event_type)
    if tier is not None:
        target.tier = tier
        target.approval_required = tier > 1
