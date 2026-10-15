"""
Tenancy and AI-provenance dependencies, shared by every router.

Every inbound request carries X-Tenant-Id; every service scopes every
query to that tenant. This service additionally stamps X-AI-Initiated on
every outbound call it makes to an execution service, so audit logs
downstream can distinguish AI-driven actions from human-driven ones.
"""
from fastapi import Header, HTTPException

from src.core import prompts


async def get_tenant_id(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")
    prompts.current_tenant.set(x_tenant_id)  # selects the tenant's promoted prompts (AI-003)
    return x_tenant_id


async def get_current_actor(x_actor_id: str | None = Header(default=None)) -> str:
    """Identifies the human operator making a request (e.g. approving a
    recommendation). Falls back to 'system' only for internal/test calls."""
    return x_actor_id or "system"
