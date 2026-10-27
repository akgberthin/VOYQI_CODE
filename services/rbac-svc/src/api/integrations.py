import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import crypto
from src.core.config import get_settings
from src.core.database import get_db
from src.core.security import get_tenant_id
from src.services import integration_engine

router = APIRouter(prefix="/api/v1/rbac/integrations", tags=["integrations"])
# Not routed by the gateway: only other services, presenting the shared
# internal token, can read decrypted credentials.
internal = APIRouter(prefix="/internal/integrations", tags=["internal"])


class IntegrationSave(BaseModel):
    values: dict = {}
    enabled: bool = True


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, integration_engine.UnknownProvider):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, integration_engine.IncompleteConfig):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, crypto.EncryptionUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


@router.get("")
async def list_integrations(tenant_id: str = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await integration_engine.list_all(db, tenant_id)
    except crypto.EncryptionUnavailable as exc:
        raise _errors(exc) from exc


@router.put("/{provider}")
async def save_integration(provider: str, body: IntegrationSave, tenant_id: str = Depends(get_tenant_id),
                           x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        return await integration_engine.save(db, tenant_id, provider, body.values, body.enabled, x_user_id or "super-admin")
    except (integration_engine.UnknownProvider, integration_engine.IncompleteConfig, crypto.EncryptionUnavailable) as exc:
        raise _errors(exc) from exc


@router.delete("/{provider}", status_code=204)
async def remove_integration(provider: str, tenant_id: str = Depends(get_tenant_id),
                             x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        await integration_engine.remove(db, tenant_id, provider, x_user_id or "super-admin")
    except integration_engine.UnknownProvider as exc:
        raise _errors(exc) from exc


@router.post("/{provider}/test")
async def test_integration(provider: str, tenant_id: str = Depends(get_tenant_id),
                           x_user_id: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    try:
        return await integration_engine.test(db, tenant_id, provider, x_user_id or "super-admin")
    except (integration_engine.UnknownProvider, integration_engine.IncompleteConfig, crypto.EncryptionUnavailable) as exc:
        raise _errors(exc) from exc


def _require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_token
    if not expected or not x_internal_token or not hmac.compare_digest(expected, x_internal_token):
        raise HTTPException(status_code=403, detail="internal endpoint")


@internal.get("/{provider}", dependencies=[Depends(_require_internal)])
async def resolved_integration(provider: str, tenant_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """{tenant_id: config} for every tenant with this integration enabled
    (or one tenant with ?tenant_id=)."""
    try:
        return await integration_engine.resolved(db, provider, tenant_id)
    except (integration_engine.UnknownProvider, crypto.EncryptionUnavailable) as exc:
        raise _errors(exc) from exc
