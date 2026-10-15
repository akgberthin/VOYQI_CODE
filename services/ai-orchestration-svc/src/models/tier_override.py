"""AI-005 — a tenant's change to one action's tier or threshold, layered
over tier-policy.yaml. Tier-3 actions can never have one (enforced in
core/tier_overrides.py and again in tiering.get_tier)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import GUID, Base


class TierOverride(Base):
    __tablename__ = "tier_overrides"
    __table_args__ = (UniqueConstraint("tenant_id", "policy_agent", "action_type", name="uq_tier_override_action"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
