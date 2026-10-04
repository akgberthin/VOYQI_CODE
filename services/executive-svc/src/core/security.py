from fastapi import Header, HTTPException, status


async def get_tenant_id(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> str:
    """Hard-requires the X-Tenant-Id header, matching every other
    service on this platform — every table here is tenant-scoped, and
    silence is never treated as a valid tenant."""
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-Id header is required"
        )
    return x_tenant_id
