import asyncio
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fateforger.agents.haunters.bootstrap import PlanningBootstrapHaunter
from fateforger.agents.planning import PlanningAgent
from fateforger.core.scheduler import get_scheduler, reset_scheduler
from fateforger.infra import Base


@pytest.fixture(scope="session")
def event_loop(request):
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def sqlite_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
        async def chat_postMessage(self, channel, text):
            return {"ts": "1"}

        async def chat_scheduleMessage(self, channel, text, post_at, thread_ts=None):
            return {"scheduled_message_id": "sched1"}

        async def chat_deleteScheduledMessage(self, channel, scheduled_message_id):
            return {"ok": True}

    return Slack()


@pytest.fixture(scope="session")
def mock_openai(monkeypatch):
    class Dummy:
        pass

    monkeypatch.setattr("openai.AsyncOpenAI", Dummy)


@pytest_asyncio.fixture()
async def scheduler():
    reset_scheduler()
    sched = get_scheduler()
    yield sched
    sched.remove_all_jobs()
    reset_scheduler()


@pytest_asyncio.fixture()
async def bootstrap_haunter(db_session, mock_slack_client, scheduler, mocker):
    client = AsyncClient(base_url="http://testserver")
    planner = PlanningAgent(client)
    mocker.patch.object(planner, "_create_event", autospec=True)
    haunter = PlanningBootstrapHaunter(
        1, mock_slack_client, scheduler, db_session, planner
    )
    yield haunter
    await client.aclose()


@pytest.fixture()
def mock_mcp(httpx_mock):
    httpx_mock.assert_all_called = False
    httpx_mock.add_response(
        url="http://testserver/mcp/create_event", json={"id": "evt"}
    )
    return httpx_mock
