"""
Test configuration and fixtures for the productivity bot test suite.
"""

import os
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

# Set default environment variables before importing application modules
TEST_ENV_VARS = {
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "SLACK_SIGNING_SECRET": "test-signing-secret",
    "SLACK_APP_TOKEN": "xapp-test-app-token",
    "OPENAI_API_KEY": "test-openai-key",
    "CALENDAR_WEBHOOK_URL": "https://test.webhook.url",
    "PORT": "8000",
    "DEBUG": "true",
    "CALENDAR_WEBHOOK_SECRET": "secret",
    "DATABASE_URL": "sqlite+aiosqlite:///test.db",
}

os.environ.update(TEST_ENV_VARS)

from productivity_bot.common import Base, Config


@pytest.fixture
def mock_env_vars():
    """Return the test environment variables."""
    return TEST_ENV_VARS


@pytest.fixture
def test_config(mock_env_vars):
    """Create a test configuration instance."""
    return Config()


@pytest.fixture
def mock_slack_app():
    """Mock Slack app for testing."""
    app = Mock()
    app.client = Mock()
    app.client.chat_postMessage = Mock()
    return app


@pytest.fixture
def sample_slack_message():
    """Sample Slack message for testing."""
    return {
        "user": "U123456789",
        "text": "remind me to submit report in 2 hours",
        "channel": "C123456789",
        "ts": "1234567890.123456",
    }


@pytest.fixture
def sample_calendar_event():
    """Sample calendar event for testing."""
    return {
        "id": "event123",
        "summary": "Team Meeting",
        "start": {"dateTime": "2023-07-16T14:00:00Z"},
        "end": {"dateTime": "2023-07-16T15:00:00Z"},
        "attendees": [{"email": "user@example.com"}],
    }


@pytest.fixture
def sample_webhook_data():
    """Sample webhook data for testing."""
    return {
        "source": "google_calendar",
        "channel_id": "test-channel-123",
        "resource_id": "test-resource-456",
        "state": "exists",
        "timestamp": "2023-07-16T10:00:00Z",
    }


# In-memory database for model tests
@pytest.fixture(scope="session")
def memory_engine():
    """Provide an in-memory SQLite engine for tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(memory_engine):
    """Return a SQLAlchemy session factory bound to the memory engine."""
    return sessionmaker(bind=memory_engine)


@pytest.fixture
def db_session(session_factory):
    """Provide a database session for a test."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def calendar_tool():
    """Return a MCPCalendarTool instance."""
    from src.productivity_bot.autogen_planner import MCPCalendarTool

    return MCPCalendarTool()


@pytest.fixture
def planner_agent():
    """Return an AutoGenPlannerAgent with mocked config."""
    from src.productivity_bot.autogen_planner import AutoGenPlannerAgent

    with patch("src.productivity_bot.autogen_planner.get_config") as mock_config:
        mock_config.return_value.openai_api_key = "test_key"
        yield AutoGenPlannerAgent()


@pytest.fixture
def planner_bot(test_config, mock_slack_app):
    """Return a PlannerBot instance using the mock Slack app."""
    from productivity_bot.planner_bot import PlannerBot

    with patch("productivity_bot.planner_bot.AsyncApp") as mock_app_class:
        mock_app_class.return_value = mock_slack_app
        yield PlannerBot(test_config)


# Async test helpers
@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
