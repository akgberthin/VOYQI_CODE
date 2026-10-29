"""Open pixel and tracked-link redirect (public — opened from inboxes and
phones with no session), and the best-send-times view built on them."""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import channel_policy, tracking

public_router = APIRouter(prefix="/api/v1/marketing/public/t", tags=["tracking"])
router = APIRouter(prefix="/api/v1/marketing/analytics", tags=["tracking"])

NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


@public_router.get("/o/{tok}.gif")
async def opened(tok: str, db: AsyncSession = Depends(get_db)):
    """Always answers with the pixel, so a bad token reveals nothing."""
    rid = tracking.parse_token(tok)
    if rid:
        await tracking.record(db, rid, "open")
    return Response(content=tracking.PIXEL, media_type="image/gif", headers=NO_STORE)


@public_router.get("/c/{tok}")
async def clicked(tok: str, u: str = Query(...), s: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Redirects only to the exact URL the link was signed for."""
    rid = tracking.parse_token(tok)
    if rid is None or tracking.link_sig(tok, u) != s or urlparse(u).scheme not in ("http", "https"):
        raise HTTPException(status_code=404, detail="link not found")
    await tracking.record(db, rid, "click")
    return RedirectResponse(u, status_code=302, headers=NO_STORE)


@router.get("/send-times")
async def send_times(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    """Email open rate by weekday × hour sent, in the tenant's timezone."""
    policy = await channel_policy.get(db, tenant_id)
    return await tracking.send_times(db, tenant_id, policy.timezone)
