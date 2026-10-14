"""
Async database engine/session setup.

Follows the platform-wide convention documented across every other
service: a cross-dialect UUID type decorator so the same models run
against SQLite (tests) and PostgreSQL (production) without branching
model code.
"""
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import CHAR, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    """Platform-standard cross-dialect UUID: native UUID on Postgres, CHAR(36) on SQLite."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            return str(uuid.UUID(str(value)))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_models() -> None:
    """SQLite only (tests, quick local runs). On Postgres the schema is
    owned by Alembic — the container runs `alembic upgrade head` before
    starting — so create_all can never mask a missing migration."""
    if engine.url.get_backend_name() != "sqlite":
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
