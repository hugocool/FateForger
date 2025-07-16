"""
Common utilities and shared functionality for the productivity bot.
"""

import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class Config:
    """Configuration management for the productivity bot."""

    def __init__(self):
        self.slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        self.slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        self.slack_app_token = os.getenv("SLACK_APP_TOKEN")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.calendar_webhook_url = os.getenv("CALENDAR_WEBHOOK_URL")
        self.port = int(os.getenv("PORT", "8000"))
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

    def validate(self) -> bool:
        """Validate that required environment variables are set."""
        required_vars = ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_APP_TOKEN"]

        missing_vars = []
        for var in required_vars:
            if not getattr(self, var.lower()):
                missing_vars.append(var)

        if missing_vars:
            logger.error(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )
            return False

        return True


def get_timestamp() -> str:
    """Get current timestamp as formatted string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Safely get environment variable with optional default."""
    value = os.getenv(key, default)
    if value is None:
        logger.warning(f"Environment variable {key} not set")
    return value


def format_slack_message(
    message: str, username: str = "ProductivityBot"
) -> Dict[str, Any]:
    """Format a message for Slack with standard formatting."""
    return {
        "text": message,
        "username": username,
        "icon_emoji": ":robot_face:",
        "timestamp": get_timestamp(),
    }
