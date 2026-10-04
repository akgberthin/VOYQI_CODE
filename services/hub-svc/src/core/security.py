from fastapi import Header, HTTPException


async def get_tenant_id(x_tenant_id: str | None = Header(default=None)) -> str:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")
    return x_tenant_id


async def get_ai_initiated(x_ai_initiated: str | None = Header(default=None)) -> str | None:
    """Non-None when the request originated from ai-orchestration-svc rather
    than a human operator — audit rows record this distinction explicitly."""
    return x_ai_initiated
