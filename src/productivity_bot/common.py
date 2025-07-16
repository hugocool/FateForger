"""
Shared utilities, configuration, and logging for the productivity bot.
"""

import logging
import os
from typing import Optional
from pydantic import BaseSettings, Field


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

    # APScheduler Settings
    scheduler_timezone: str = Field("UTC", env="SCHEDULER_TIMEZONE")

    class Config:
        env_file = ".env"
        case_sensitive = False


def get_config() -> Config:
    """Get application configuration."""
    return Config()


def setup_logging(level: str = None) -> logging.Logger:
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
