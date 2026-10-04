from fastapi import Header, HTTPException, status


async def get_tenant_id(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> str:
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id header is required"
        )
    return x_tenant_id
