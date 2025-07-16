"""
Shared utilities, configuration, and logging for the productivity bot.
"""

import logging
import os
from typing import Optional, List, Dict, Any
from pydantic_settings import BaseSettings
from pydantic import Field
from sqlalchemy.orm import DeclarativeBase
import httpx


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class Config(BaseSettings):
    """Application configuration using Pydantic settings."""

    # Slack Configuration
    slack_bot_token: str = Field(..., env="SLACK_BOT_TOKEN")
    slack_signing_secret: str = Field(..., env="SLACK_SIGNING_SECRET")
    slack_app_token: Optional[str] = Field(None, env="SLACK_APP_TOKEN")

    # OpenAI Configuration
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4", env="OPENAI_MODEL")

    # MCP Configuration
    mcp_endpoint: str = Field("http://mcp:4000", env="MCP_ENDPOINT")

    # Calendar Configuration
    calendar_webhook_secret: str = Field(..., env="CALENDAR_WEBHOOK_SECRET")

    # Application Settings
    environment: str = Field("production", env="ENVIRONMENT")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    development: bool = Field(False, env="DEVELOPMENT")

    # Database Settings
    database_url: str = Field(..., env="DATABASE_URL")

    # APScheduler Settings
    scheduler_timezone: str = Field("UTC", env="SCHEDULER_TIMEZONE")

    class Config:
        env_file = ".env"
        case_sensitive = False


def get_config() -> Config:
    """Get application configuration."""
    return Config()


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Setup application logging."""
    config = get_config()
    log_level = level or config.log_level

    # Configure logging format
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/admonish.log")],
    )

    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    return logging.getLogger("admonish")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(f"admonish.{name}")


# MCP Calendar Integration Functions
async def mcp_query(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query the MCP server for calendar operations.
    """
    config = get_config()
    mcp_url = config.mcp_endpoint

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{mcp_url}/mcp", json=request, timeout=30.0)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger = get_logger("mcp_client")
        logger.error(f"MCP query failed: {e}")
        return {}


async def list_events_since(
    calendar_id: str, sync_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    List calendar events changed since the last sync token via MCP server.
    """
    try:
        query_params = {
            "calendar_id": calendar_id,
            "maxResults": 250,
            "singleEvents": True,
            "orderBy": "updated",
        }

        if sync_token:
            query_params["syncToken"] = sync_token
        else:
            # Get events from last 30 days if no sync token
            from datetime import datetime, timedelta

            time_min = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
            query_params["timeMin"] = time_min

        # Use MCP to query calendar events
        mcp_request = {"tool": "calendar.events.list", "arguments": query_params}

        logger = get_logger("calendar_sync")
        logger.info(f"Querying MCP for events since sync_token: {sync_token}")

        response = await mcp_query(mcp_request)

        events = response.get("events", [])
        next_sync_token = response.get("nextSyncToken")

        logger.info(
            f"Retrieved {len(events)} events from MCP, next sync token: {next_sync_token}"
        )

        return {"events": events, "nextSyncToken": next_sync_token}

    except Exception as e:
        logger = get_logger("calendar_sync")
        logger.error(f"Error listing events via MCP: {e}")
        return {"events": [], "nextSyncToken": None}


async def watch_calendar(calendar_id: str, webhook_url: str) -> Dict[str, Any]:
    """
    Set up a watch channel for calendar changes via MCP server.
    """
    try:
        import uuid
        from datetime import datetime, timedelta

        # Generate unique channel ID
        channel_id = str(uuid.uuid4())

        # Set expiration 1 week from now (max for Calendar API)
        expiration = int((datetime.utcnow() + timedelta(days=7)).timestamp() * 1000)

        mcp_request = {
            "tool": "calendar.events.watch",
            "arguments": {
                "calendar_id": calendar_id,
                "webhook_url": webhook_url,
                "channel_id": channel_id,
                "expiration": expiration,
            },
        }

        logger = get_logger("calendar_sync")
        logger.info(f"Setting up watch channel {channel_id} for calendar {calendar_id}")

        response = await mcp_query(mcp_request)

        logger.info(f"Watch channel created: {response}")
        return response

    except Exception as e:
        logger = get_logger("calendar_sync")
        logger.error(f"Error setting up calendar watch via MCP: {e}")
        return {}
