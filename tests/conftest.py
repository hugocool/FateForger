"""
Test configuration and fixtures for the productivity bot test suite.
"""

import pytest
import os
from unittest.mock import Mock, patch
from productivity_bot.common import Config


@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing."""
    env_vars = {
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "SLACK_SIGNING_SECRET": "test-signing-secret",
        "SLACK_APP_TOKEN": "xapp-test-app-token",
        "OPENAI_API_KEY": "test-openai-key",
        "CALENDAR_WEBHOOK_SECRET": "test-webhook-secret",
        "DATABASE_URL": "sqlite+aiosqlite:///./test.db",
        "PORT": "8000",
        "DEBUG": "true",
    }

    with patch.dict(os.environ, env_vars, clear=True):
        yield env_vars


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


# Test database setup (if needed later)
@pytest.fixture(scope="session")
def test_db():
    """Setup test database if needed."""
    # For now, return None as we're not using a database yet
    yield None


# Async test helpers
@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
