from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.models import DecisionLog
from src.services import portfolio_intelligence_agent

router = APIRouter(prefix="/api/v1/ai/portfolio", tags=["portfolio-intelligence"])


@router.post("/reports")
async def generate_report(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """On-demand cross-domain portfolio report — the same report the
    scheduled portfolio.rollup.scheduled run produces."""
    result = await portfolio_intelligence_agent.generate_portfolio_report(db, tenant_id, "portfolio.report_requested")
    return {
        "decision_log_id": str(result.decision_log.id), "report": result.report,
        "summary": result.decision_log.reasoning, "skipped_reason": result.skipped_reason,
    }


@router.get("/reports/latest")
async def latest_report(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DecisionLog)
        .where(
            DecisionLog.tenant_id == tenant_id, DecisionLog.agent_key == portfolio_intelligence_agent.AGENT_KEY,
            DecisionLog.action_taken == "portfolio_report_generated",
        )
        .order_by(DecisionLog.created_at.desc())
        .limit(1)
    )
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=404, detail="No portfolio report yet")
    return {"decision_log_id": str(log.id), "generated_at": log.created_at, "report": log.trigger_context.get("report"), "summary": log.reasoning}
