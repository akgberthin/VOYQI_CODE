"""Tenant security policy set in Super Admin → Platform settings, and
enforced by the services rather than only displayed:

- require_mfa_for_operators: rbac-svc grants no permissions to an operator
  whose sign-in account has no MFA (they can still reach /api/v1/auth/* to
  enrol, which the gateway allows to every signed-in user).
- cors_allowed_origins: browser origins the gateway answers CORS for
  (it reads the union across tenants over /internal/).

Stored as PlatformSetting(category=security, key=policy). The generic
settings endpoint refuses that category so every change is validated here.
"""
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import PlatformSetting, SettingCategory

KEY = "policy"
DEFAULTS = {"require_mfa_for_operators": False, "cors_allowed_origins": []}
MAX_ORIGINS = 50


class PolicyError(ValueError):
    pass


def clean_origin(raw: str) -> str:
    """An origin is scheme://host[:port] — no path, no wildcard. https only,
    except http for localhost during development."""
    o = raw.strip().rstrip("/")
    parts = urlsplit(o)
    if "*" in o:
        raise PolicyError(f"{raw!r}: wildcards aren't allowed — list each origin.")
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise PolicyError(f"{raw!r} isn't an origin like https://app.voyqi.com")
    if parts.path or parts.query or parts.fragment or parts.username:
        raise PolicyError(f"{raw!r}: an origin has no path — use {parts.scheme}://{parts.netloc}")
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise PolicyError(f"{raw!r}: use https (plain http is only allowed for localhost).")
    return f"{parts.scheme}://{parts.netloc.lower()}"


def clean(value: dict) -> dict:
    out = dict(DEFAULTS)
    unknown = set(value) - set(DEFAULTS)
    if unknown:
        raise PolicyError(f"Unknown security settings: {', '.join(sorted(unknown))}")
    if "require_mfa_for_operators" in value:
        if not isinstance(value["require_mfa_for_operators"], bool):
            raise PolicyError("require_mfa_for_operators must be true or false")
        out["require_mfa_for_operators"] = value["require_mfa_for_operators"]
    if "cors_allowed_origins" in value:
        origins = value["cors_allowed_origins"]
        if not isinstance(origins, list) or not all(isinstance(o, str) for o in origins):
            raise PolicyError("cors_allowed_origins must be a list of origins")
        cleaned = list(dict.fromkeys(clean_origin(o) for o in origins if o.strip()))
        if len(cleaned) > MAX_ORIGINS:
            raise PolicyError(f"At most {MAX_ORIGINS} origins.")
        out["cors_allowed_origins"] = cleaned
    return out


async def _row(db: AsyncSession, tenant_id: str) -> PlatformSetting | None:
    q = select(PlatformSetting).where(PlatformSetting.tenant_id == tenant_id, PlatformSetting.category == SettingCategory.SECURITY, PlatformSetting.key == KEY)
    return (await db.execute(q)).scalar_one_or_none()


async def get(db: AsyncSession, tenant_id: str) -> dict:
    row = await _row(db, tenant_id)
    return {**DEFAULTS, **(row.value if row else {})}


async def save(db: AsyncSession, tenant_id: str, value: dict, updated_by: str) -> tuple[dict, dict]:
    """Merges and validates. Returns (new policy, {field: (old, new)})."""
    current = await get(db, tenant_id)
    new = clean({**current, **value})
    diff = {k: (current[k], new[k]) for k in new if current.get(k) != new[k]}
    row = await _row(db, tenant_id)
    if row is None:
        db.add(PlatformSetting(tenant_id=tenant_id, category=SettingCategory.SECURITY, key=KEY, value=new, updated_by=updated_by))
    else:
        row.value, row.updated_by = new, updated_by
    await db.commit()
    return new, diff


async def all_cors_origins(db: AsyncSession) -> list[str]:
    q = select(PlatformSetting.value).where(PlatformSetting.category == SettingCategory.SECURITY, PlatformSetting.key == KEY)
    origins: set[str] = set()
    for (value,) in (await db.execute(q)).all():
        origins.update((value or {}).get("cors_allowed_origins") or [])
    return sorted(origins)
