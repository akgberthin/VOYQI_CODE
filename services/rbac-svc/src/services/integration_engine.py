"""Stores, masks, tests and serves integration credentials."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import crypto
from src.models import IntegrationCredential
from src.services import audit_engine, integration_checks
from src.services.integration_catalog import CATALOG, missing_required, secret_keys, with_defaults


class UnknownProvider(Exception):
    pass


class IncompleteConfig(Exception):
    pass


def _check_provider(provider: str) -> None:
    if provider not in CATALOG:
        raise UnknownProvider(f"unknown integration '{provider}'")


async def _row(db: AsyncSession, tenant_id: str, provider: str) -> IntegrationCredential | None:
    result = await db.execute(select(IntegrationCredential).where(IntegrationCredential.tenant_id == tenant_id, IntegrationCredential.provider == provider))
    return result.scalar_one_or_none()


def masked(provider: str, row: IntegrationCredential | None) -> dict:
    """What the API shows: plain fields as entered, secrets only as set/unset."""
    config = crypto.decrypt(row.config_encrypted) if row else {}
    secrets = secret_keys(provider)
    values = {}
    for field in CATALOG[provider]["fields"]:
        k = field["key"]
        if k in secrets:
            v = str(config.get(k) or "")
            values[k] = {"set": bool(v), "preview": f"…{v[-4:]}" if len(v) >= 12 else ("set" if v else "")}
        else:
            values[k] = config.get(k, field.get("default", ""))
    return {
        "provider": provider, **{k: v for k, v in CATALOG[provider].items() if k != "fields"}, "fields": CATALOG[provider]["fields"],
        "configured": row is not None, "enabled": row.enabled if row else False, "values": values,
        "test_status": row.test_status if row else "untested", "test_detail": row.test_detail if row else "",
        "tested_at": row.tested_at if row else None, "updated_by": row.updated_by if row else "", "updated_at": row.updated_at if row else None,
    }


async def list_all(db: AsyncSession, tenant_id: str) -> list[dict]:
    rows = {r.provider: r for r in (await db.execute(select(IntegrationCredential).where(IntegrationCredential.tenant_id == tenant_id))).scalars()}
    return [masked(p, rows.get(p)) for p in CATALOG]


async def save(db: AsyncSession, tenant_id: str, provider: str, values: dict, enabled: bool, actor: str) -> dict:
    """Merges into the stored config: a secret left blank keeps its stored
    value, so an admin can change one field without re-entering keys."""
    _check_provider(provider)
    row = await _row(db, tenant_id, provider)
    current = crypto.decrypt(row.config_encrypted) if row else {}
    known = {f["key"] for f in CATALOG[provider]["fields"]}
    merged = dict(current)
    for k, v in values.items():
        if k not in known:
            continue
        if k in secret_keys(provider) and (v is None or str(v).strip() == ""):
            continue
        merged[k] = str(v).strip() if v is not None else ""
    merged = with_defaults(provider, merged)
    missing = missing_required(provider, merged)
    if missing:
        raise IncompleteConfig(f"{CATALOG[provider]['label']} needs: {', '.join(missing)}")
    if row is None:
        row = IntegrationCredential(tenant_id=tenant_id, provider=provider, config_encrypted="")
        db.add(row)
    row.config_encrypted = crypto.encrypt(merged)
    row.enabled = enabled
    row.updated_by = actor
    row.test_status, row.test_detail, row.tested_at = "untested", "", None
    await db.commit()
    await db.refresh(row)
    changed = sorted(k for k in values if k in known)
    # Field names only — never values — go to the audit log.
    await audit_engine.record(db, tenant_id, "human", actor, "integration.updated", provider, f"fields={','.join(changed)} enabled={enabled}")
    return masked(provider, row)


async def remove(db: AsyncSession, tenant_id: str, provider: str, actor: str) -> bool:
    _check_provider(provider)
    row = await _row(db, tenant_id, provider)
    if row is None:
        return False
    await db.delete(row)
    await db.commit()
    await audit_engine.record(db, tenant_id, "human", actor, "integration.removed", provider)
    return True


async def test(db: AsyncSession, tenant_id: str, provider: str, actor: str) -> dict:
    _check_provider(provider)
    row = await _row(db, tenant_id, provider)
    if row is None:
        raise IncompleteConfig(f"{CATALOG[provider]['label']} isn't configured")
    ok, detail = await integration_checks.run(provider, crypto.decrypt(row.config_encrypted))
    row.test_status, row.test_detail, row.tested_at = ("ok" if ok else "failed"), detail, datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    await audit_engine.record(db, tenant_id, "human", actor, "integration.tested", provider, row.test_status)
    return masked(provider, row)


async def resolved(db: AsyncSession, provider: str, tenant_id: str | None = None) -> dict:
    """Decrypted, enabled configs for services: {tenant_id: config}."""
    _check_provider(provider)
    query = select(IntegrationCredential).where(IntegrationCredential.provider == provider, IntegrationCredential.enabled.is_(True))
    if tenant_id:
        query = query.where(IntegrationCredential.tenant_id == tenant_id)
    return {r.tenant_id: crypto.decrypt(r.config_encrypted) for r in (await db.execute(query)).scalars()}
