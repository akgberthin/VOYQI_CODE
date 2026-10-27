import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.database import Base, get_db
from src.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


TENANT_HEADERS = {"X-Tenant-Id": "voyqi-holdings"}


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_delivery(monkeypatch):
    """Tests never reach notification-svc: by default deliveries behave as
    it does with no provider configured (logged, console mode). Tests
    about delivery replace this with their own stand-in."""
    from src.services import delivery

    async def console(tenant_id, channel, recipient, body, subject, source):
        return {"status": "sent", "delivery_mode": "console", "error_message": None}

    monkeypatch.setattr(delivery, "deliver", console)
