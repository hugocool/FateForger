import asyncio

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from fateforger.agents.admonisher.models import Base as AdmonisherBase
from apscheduler.schedulers.asyncio import AsyncIOScheduler


@pytest.fixture(scope="session")
def event_loop(request):
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def sqlite_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AdmonisherBase.metadata.create_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(sqlite_engine):
    async_session = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture(scope="session")
def mock_slack_client():
    class Slack:
        async def chat_postMessage(self, channel, text, thread_ts=None):
            return {"ts": "1"}

        async def chat_scheduleMessage(self, channel, text, post_at, thread_ts=None):
            return {"scheduled_message_id": "sched1"}

        async def chat_deleteScheduledMessage(self, channel, scheduled_message_id):
            return {"ok": True}

    return Slack()


@pytest.fixture()
def mock_slack(mock_slack_client):
    return mock_slack_client


@pytest.fixture()
def mock_scheduler():
    return MagicMock()


@pytest_asyncio.fixture()
async def scheduler():
    sched = AsyncIOScheduler()
    sched.start()
    yield sched
    sched.shutdown(wait=False)
