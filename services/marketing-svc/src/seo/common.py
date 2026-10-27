"""Shared plumbing of the SEO engine: the error type the API turns into
{detail, code}, who is acting, the activity feed, events and
serialisation."""
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.kafka_events import publish_event
from src.seo.constants import AGENT_ACTOR_PREFIX, INTEGRATION_KINDS
from src.seo.models import SeoActivity, SeoIntegration, SeoSite

EVENT_TOPIC = "marketing.seo"


class SeoError(Exception):
    """An answer the API returns as {detail, code, ...extra}.
    code: not_found / invalid / conflict / hard_gate / provider_not_configured / provider_error."""

    def __init__(self, status: int, detail: str, code: str, **extra: Any):
        super().__init__(detail)
        self.status, self.detail, self.code, self.extra = status, detail, code, extra


def not_found(what: str) -> SeoError:
    return SeoError(404, f"{what} not found", "not_found")


def invalid(detail: str, **extra: Any) -> SeoError:
    return SeoError(422, detail, "invalid", **extra)


def conflict(detail: str) -> SeoError:
    return SeoError(409, detail, "conflict")


def hard_gate(detail: str) -> SeoError:
    return SeoError(409, detail, "hard_gate")


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything stored is UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def days_ago(days: float) -> datetime:
    return now() - timedelta(days=days)


def actor_of(user_id: str | None, ai_initiated: str | None) -> str:
    """'ai:<agent>' for agent requests, otherwise the person."""
    if ai_initiated:
        name = ai_initiated if ai_initiated not in ("true", "1", "yes") else "agent"
        return AGENT_ACTOR_PREFIX + name.removeprefix(AGENT_ACTOR_PREFIX)
    return user_id or "marketing-admin"


def is_agent(actor: str) -> bool:
    return actor.startswith(AGENT_ACTOR_PREFIX)


def to_json(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return aware(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json(v) for v in value]
    return value


def row(obj: Any, *, exclude: tuple[str, ...] = ()) -> dict:
    return {c.key: to_json(getattr(obj, c.key)) for c in obj.__table__.columns if c.key not in exclude}


def parse_id(value: str | uuid.UUID, what: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise not_found(what) from exc


async def get_site(db: AsyncSession, tenant_id: str, site_id: str | uuid.UUID) -> SeoSite:
    site = (await db.execute(select(SeoSite).where(SeoSite.id == parse_id(site_id, "site"), SeoSite.tenant_id == tenant_id))).scalar_one_or_none()
    if site is None:
        raise not_found("site")
    return site


async def get_owned(db: AsyncSession, model: Any, tenant_id: str, obj_id: str | uuid.UUID, what: str) -> Any:
    obj = (await db.execute(select(model).where(model.id == parse_id(obj_id, what), model.tenant_id == tenant_id))).scalar_one_or_none()
    if obj is None:
        raise not_found(what)
    return obj


async def integration(db: AsyncSession, site: SeoSite, kind: str) -> SeoIntegration:
    """The site's row for a kind, created as not_connected on first use."""
    found = (await db.execute(select(SeoIntegration).where(SeoIntegration.site_id == site.id, SeoIntegration.kind == kind))).scalar_one_or_none()
    if found is None:
        found = SeoIntegration(tenant_id=site.tenant_id, site_id=site.id, kind=kind, status="not_connected", config={})
        db.add(found)
        await db.flush()
    return found


async def connected(db: AsyncSession, site: SeoSite, kind: str) -> SeoIntegration | None:
    """The integration when it has passed its test, else None."""
    integ = await integration(db, site, kind)
    return integ if integ.status == "connected" else None


def integration_out(integ: SeoIntegration) -> dict:
    label, powers, providers = INTEGRATION_KINDS[integ.kind]
    out = row(integ, exclude=("id", "tenant_id", "site_id"))
    out.update(label=label, powers=powers, providers=list(providers),
               adapter_available=bool(integ.provider) and integ.provider in providers)
    return out


async def activity(db: AsyncSession, site: SeoSite, kind: str, message: str, actor: str,
                   ref_type: str | None = None, ref_id: Any = None) -> None:
    db.add(SeoActivity(tenant_id=site.tenant_id, site_id=site.id, kind=kind, message=message[:2000], actor=actor,
                       ref_type=ref_type, ref_id=str(ref_id) if ref_id is not None else None))


async def emit(event_type: str, site: SeoSite, payload: dict) -> None:
    """Tells the AI service (and anything else listening) what happened."""
    await publish_event(EVENT_TOPIC, event_type, site.tenant_id, {"site_id": str(site.id), **to_json(payload)})
