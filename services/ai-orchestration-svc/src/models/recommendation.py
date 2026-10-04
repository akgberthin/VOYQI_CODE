"""
AIRecommendation — AI-002 Recommendation Review & Override.

Every action a specialist agent proposes lands here first if it meets or
exceeds the owning agent's human_approval_threshold. Below threshold, the
agent may act autonomously (auto_executed=True) but the row still exists
for the audit trail — nothing an agent does is invisible.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class RecommendationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    AUTO_EXECUTED = "auto_executed"


class AIRecommendation(Base):
    __tablename__ = "ai_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    agent_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # The module/domain this recommendation targets, e.g. "rbac", "pricing".
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # The specific action proposed, e.g. "revoke_permission_grant".
    action_type: Mapped[str] = mapped_column(String(128), nullable=False)

    # Free-form structured payload describing exactly what the agent
    # proposes to do — the arguments it would pass to the downstream
    # execution-service tool call.
    proposed_action: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Explainability: why the agent proposed this.
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    drivers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Link back to the domain service's own record, if one exists
    # (e.g. pricing-svc's PricingRecommendation.id). Nullable — not every
    # domain has its own parallel record.
    external_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(RecommendationStatus), nullable=False, default=RecommendationStatus.PENDING
    )
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Autonomy tier this recommendation was created under (2 or 3 — a
    # tier-1 action never reaches AI-002 at all, see
    # docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md §1.2). Nullable for
    # the same reason as DecisionLog.tier — not every recommendation
    # path is migrated to the tier lookup yet.
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Tier 3 only: approval is structurally blocked (src/api/recommendations.py)
    # until a named accountable_role is supplied — a compliance boundary,
    # not a UX preference. See AIRecommendation's own tier-3 examples:
    # credit decisions, employment decisions, ABAC policy grants.
    requires_accountable_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accountable_role: Mapped[str | None] = mapped_column(String(128), nullable=True)

    triggering_event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    triggering_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
