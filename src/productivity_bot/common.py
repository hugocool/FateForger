"""
Shared utilities, configuration, and logging for the productivity bot.
"""

import logging
import os
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings
from slack_bolt.async_app import AsyncApp
from sqlalchemy.orm import DeclarativeBase


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


def get_slack_app() -> AsyncApp:
    """Get a shared AsyncApp instance for Slack interactions."""
    config = get_config()
    return AsyncApp(
        token=config.slack_bot_token,
        signing_secret=config.slack_signing_secret,
    )


def setup_logging(level: Optional[str] = None) -> logging.Logger:
    """Setup application logging with proper configuration.

    Args:
        level: Optional log level override. If not provided, uses config default.

    Returns:
        Configured logger instance for the main application.

    Note:
        Creates logs directory if it doesn't exist and configures both
        console and file handlers.
    """
    config = get_config()
    log_level = level or config.log_level

    # Configure logging format
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/fateforger.log")],
    )

    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    return logging.getLogger("fateforger")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with hierarchical naming.

    Args:
        name: Logger name suffix (will be prefixed with 'fateforger.')

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(f"fateforger.{name}")


# MCP Calendar Integration Functions
async def mcp_query(request: Dict[str, Any]) -> Dict[str, Any]:
    """Query the MCP server for calendar operations.

    Args:
        request: MCP request payload containing method and parameters.

    Returns:
        Response data from MCP server, or empty dict on error.

    Raises:
        No exceptions raised - errors are logged and empty dict returned.
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


class BaseEventService:
    """Service for managing BaseEvent entities via MCP.

    This service provides CRUD operations for all calendar events through
    the Model Context Protocol (MCP) integration with Google Calendar.

    Attributes:
        logger: Logger instance for this service.
    """

    def __init__(self) -> None:
        """Initialize the BaseEventService."""
        self.logger = get_logger("base_event_service")

    async def list_events(
        self,
        calendar_id: str = "primary",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """List events from calendar via MCP.

        Args:
            calendar_id: Calendar identifier (default: "primary").
            start_time: Optional start time filter.
            end_time: Optional end time filter.

        Returns:
            List of event dictionaries from the calendar API.
        """
        request = {
            "method": "calendar.events.list",
            "params": {
                "calendarId": calendar_id,
                "timeMin": start_time.isoformat() if start_time else None,
                "timeMax": end_time.isoformat() if end_time else None,
                "singleEvents": True,
                "orderBy": "startTime",
            },
        }

        response = await mcp_query(request)
        return response.get("items", [])

    async def get_event(
        self, event_id: str, calendar_id: str = "primary"
    ) -> Optional[Dict[str, Any]]:
        """Get a specific event by ID via MCP.

        Args:
            event_id: Unique identifier for the event.
            calendar_id: Calendar identifier (default: "primary").

        Returns:
            Event dictionary if found, None otherwise.
        """
        request = {
            "method": "calendar.events.get",
            "params": {"calendarId": calendar_id, "eventId": event_id},
        }

        response = await mcp_query(request)
        return response if response.get("id") else None

    async def create_event(
        self, event_data: Dict[str, Any], calendar_id: str = "primary"
    ) -> Optional[Dict[str, Any]]:
        """Create a new event via MCP.

        Args:
            event_data: Event data dictionary matching Google Calendar API format.
            calendar_id: Calendar identifier (default: "primary").

        Returns:
            Created event dictionary if successful, None otherwise.
        """
        request = {
            "method": "calendar.events.insert",
            "params": {"calendarId": calendar_id, "resource": event_data},
        }

        response = await mcp_query(request)
        return response if response.get("id") else None

    async def update_event(
        self, event_id: str, event_data: Dict[str, Any], calendar_id: str = "primary"
    ) -> Optional[Dict[str, Any]]:
        """Update an existing event via MCP.

        Args:
            event_id: Unique identifier for the event to update.
            event_data: Updated event data dictionary.
            calendar_id: Calendar identifier (default: "primary").

        Returns:
            Updated event dictionary if successful, None otherwise.
        """
        request = {
            "method": "calendar.events.update",
            "params": {
                "calendarId": calendar_id,
                "eventId": event_id,
                "resource": event_data,
            },
        }

        response = await mcp_query(request)
        return response if response.get("id") else None


# Planning Event Management Functions
async def find_planning_event(date: date) -> Optional[Dict[str, Any]]:
    """Find the planning event for a specific date."""
    from .mcp_integration import get_mcp_client

    try:
        mcp_client = await get_mcp_client()
        if not mcp_client:
            get_logger(__name__).warning(
                "MCP client not available for find_planning_event"
            )
            return None

        # Search for events on the date with planning-related titles
        start_time = datetime.combine(date, datetime.min.time()).isoformat()
        end_time = datetime.combine(date, datetime.max.time()).isoformat()

        events = await mcp_client.list_events(start_date=start_time, end_date=end_time)

        # Look for planning events (check for bot metadata or title patterns)
        for event in events:
            title = event.get("summary", "").lower()
            description = event.get("description", "")

            # Check if this is a bot-created planning event
            if "🧠 plan tomorrow" in title or "metadata.bot_id" in description:
                return event

            # Check for planning-related keywords
            if any(
                keyword in title
                for keyword in ["plan", "planning", "tomorrow", "daily"]
            ):
                return event

        return None
    except Exception as e:
        get_logger(__name__).error(f"Error finding planning event: {e}")
        return None


async def ensure_planning_event(
    date: date, default_time: str = "17:00"
) -> Dict[str, Any]:
    """Find or create a planning event for the specified date."""
    # First try to find existing event
    existing_event = await find_planning_event(date)
    if existing_event:
        return existing_event

    # Create new planning event
    return await create_planning_event(date, default_time)


async def create_planning_event(
    date: date, default_time: str = "17:00"
) -> Dict[str, Any]:
    """Create a new planning event for the specified date."""
    from .mcp_integration import get_mcp_client

    try:
        mcp_client = await get_mcp_client()
        if not mcp_client:
            get_logger(__name__).warning(
                "MCP client not available for create_planning_event"
            )
            return {}

        # Parse time
        hour, minute = map(int, default_time.split(":"))
        start_datetime = datetime.combine(date, time(hour, minute))
        end_datetime = start_datetime + timedelta(
            minutes=30
        )  # 30-minute planning session

        title = f"🧠 Plan Tomorrow - {date.strftime('%A, %B %d')}"
        description = (
            "Daily planning session to organize tomorrow's tasks and priorities.\n\n"
            "This event is managed by the Productivity Bot.\n"
            "metadata.bot_id: productivity_bot\n"
            "metadata.agent_type: planner"
        )

        # Create event via MCP
        created_event = await mcp_client.create_event(
            title=title,
            start_time=start_datetime.isoformat(),
            end_time=end_datetime.isoformat(),
            description=description,
        )

        if not created_event:
            raise Exception(f"Failed to create planning event for {date}")

        logger = get_logger("planning_service")
        logger.info(f"Created planning event for {date}: {created_event.get('id')}")

        return created_event
    except Exception as e:
        get_logger(__name__).error(f"Error creating planning event: {e}")
        raise Exception(f"Failed to create planning event for {date}") from e


# Agent Dispatcher for Event-Driven Routing
async def dispatch_event_change(
    event_data: Dict[str, Any], change_type: str = "updated"
) -> None:
    """Route event changes to appropriate agents based on event metadata.

    Args:
        event_data: The event data from calendar API.
        change_type: Type of change (created, updated, deleted).

    Note:
        This function does not raise exceptions - errors are logged internally.
    """
    logger = get_logger("event_dispatcher")

    try:
        # Extract metadata from event
        description = event_data.get("description", "")
        title = event_data.get("summary", "")

        # Determine agent type from metadata or title
        agent_type = None
        if "metadata.agent_type:" in description:
            # Extract agent type from metadata
            for line in description.split("\n"):
                if "metadata.agent_type:" in line:
                    agent_type = line.split(":")[-1].strip()
                    break
        elif "🧠 plan" in title.lower():
            agent_type = "planner"

        logger.info(
            f"Dispatching {change_type} event {event_data.get('id')} to agent: {agent_type}"
        )

        # Route to appropriate agent
        if agent_type == "planner":
            await _dispatch_to_planner(event_data, change_type)
        elif agent_type == "haunter":
            await _dispatch_to_haunter(event_data, change_type)
        elif agent_type == "timeboxer":
            await _dispatch_to_timeboxer(event_data, change_type)
        elif agent_type == "task_creator":
            await _dispatch_to_task_creator(event_data, change_type)
        else:
            # Default: check if it needs any agent handling
            await _dispatch_to_default_handler(event_data, change_type)

    except Exception as e:
        logger.error(f"Error dispatching event change: {e}")


async def _dispatch_to_planner(event_data: Dict[str, Any], change_type: str) -> None:
    """Dispatch to PlannerAgent.

    Args:
        event_data: Event data from calendar API.
        change_type: Type of change (created, updated, deleted).
    """
    try:
        from .planner_bot import PlannerBot

        planner = PlannerBot()
        # TODO: Implement handle_event_change method in PlannerBot
        # await planner.handle_event_change(event_data, change_type)
        logger = get_logger("event_dispatcher")
        logger.info(f"Would route planning event to PlannerBot: {event_data.get('id')}")
    except Exception as e:
        logger = get_logger("event_dispatcher")
        logger.error(f"Error dispatching to planner: {e}")


async def _dispatch_to_haunter(event_data: Dict[str, Any], change_type: str) -> None:
    """Dispatch to HaunterAgent.

    Args:
        event_data: Event data from calendar API.
        change_type: Type of change (created, updated, deleted).
    """
    try:
        from .haunter_bot import HaunterBot

        haunter = HaunterBot()
        # TODO: Implement handle_event_change method in HaunterBot
        # await haunter.handle_event_change(event_data, change_type)
        logger = get_logger("event_dispatcher")
        logger.info(f"Would route haunt event to HaunterBot: {event_data.get('id')}")
    except Exception as e:
        logger = get_logger("event_dispatcher")
        logger.error(f"Error dispatching to haunter: {e}")


async def _dispatch_to_timeboxer(event_data: Dict[str, Any], change_type: str) -> None:
    """Dispatch to TimeboxerAgent (placeholder).

    Args:
        event_data: Event data from calendar API.
        change_type: Type of change (created, updated, deleted).
    """
    logger = get_logger("event_dispatcher")
    logger.info(f"Timeboxer agent not implemented yet for event {event_data.get('id')}")


async def _dispatch_to_task_creator(
    event_data: Dict[str, Any], change_type: str
) -> None:
    """Dispatch to TaskCreatorAgent (placeholder).

    Args:
        event_data: Event data from calendar API.
        change_type: Type of change (created, updated, deleted).
    """
    logger = get_logger("event_dispatcher")
    logger.info(
        f"Task creator agent not implemented yet for event {event_data.get('id')}"
    )


async def _dispatch_to_default_handler(
    event_data: Dict[str, Any], change_type: str
) -> None:
    """Default handler for events that don't match specific agents.

    Args:
        event_data: Event data from calendar API.
        change_type: Type of change (created, updated, deleted).
    """
    logger = get_logger("event_dispatcher")
    logger.debug(
        f"No specific agent for event {event_data.get('id')}, using default handling"
    )


# Global instances
_base_event_service = None


def get_base_event_service() -> BaseEventService:
    """Get singleton BaseEventService instance."""
    global _base_event_service
    if _base_event_service is None:
        _base_event_service = BaseEventService()
    return _base_event_service


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


def backoff_minutes(attempt: int) -> int:
    """
    Calculate exponential backoff delay in minutes for haunt attempts.

    Args:
        attempt: The attempt number (0-based).

    Returns:
        Number of minutes to wait before next haunt.
    """
    if attempt <= 0:
        return 5  # Initial delay: 5 minutes
    elif attempt == 1:
        return 10  # Second attempt: 10 minutes
    elif attempt == 2:
        return 20  # Third attempt: 20 minutes
    else:
        # Cap at 60 minutes for subsequent attempts
        return min(60, 20 * (2 ** (attempt - 2)))


def get_timestamp() -> str:
    """
    Get current timestamp in ISO format.

    Returns:
        str: Current timestamp in ISO format
    """
    return datetime.now().isoformat()


def safe_get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Safely get environment variable.

    Args:
        key: Environment variable key
        default: Default value if not found

    Returns:
        Environment variable value or default
    """
    return os.environ.get(key, default)


def format_slack_message(message: str, **kwargs: Any) -> str:
    """
    Format a Slack message with optional formatting.

    Args:
        message: Base message string
        **kwargs: Additional formatting parameters

    Returns:
        str: Formatted message
    """
    try:
        return message.format(**kwargs)
    except KeyError as e:
        logger.warning(f"Missing key in message formatting: {e}")
        return message
