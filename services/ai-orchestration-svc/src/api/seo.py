"""Growth / SEO agents — /api/v1/ai/seo/...

marketing-svc calls the cycle and intake routes (its scheduler and its
email inbox); the console reads the agent roster."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import seo_agent
from src.services.seo_tools import SeoApiError

router = APIRouter(prefix="/api/v1/ai/seo", tags=["seo-agents"])


@router.get("/agents")
async def agents(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """The fourteen SEO agents with what they own and whether they are on."""
    return await seo_agent.roster(db, tenant_id)


@router.post("/sites/{site_id}/cycle")
async def cycle(site_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """One autopilot cycle for a site: replan, then run every task the plan allows."""
    try:
        return await seo_agent.run_cycle(db, tenant_id, site_id)
    except SeoApiError as exc:
        raise HTTPException(status_code=502 if exc.status in (0, 500) else exc.status, detail=exc.detail) from exc


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Runs one task now, as its owning agent — still subject to the plan
    (the claim is refused if the site's rules hold it)."""
    try:
        return await seo_agent.run_task(db, tenant_id, task_id)
    except SeoApiError as exc:
        raise HTTPException(status_code=502 if exc.status in (0, 500) else exc.status, detail=exc.detail) from exc


@router.post("/requests/{request_id}/intake")
async def intake(request_id: str, tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await seo_agent.intake(db, tenant_id, request_id)
