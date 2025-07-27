from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration using environment variables."""

    # Core Configuration
    slack_bot_token: str = Field(default="x")
    slack_signing_secret: str = Field(default="x")
    openai_api_key: str = Field(default="x")
    database_url: str = Field(default="sqlite:///:memory:")

    # MCP Server Configuration
    mcp_version: str = Field(default="v1.4.8")
    port: str = Field(default="3000")
    transport: str = Field(default="stdio")
    google_oauth_credentials: str = Field(default="/app/gcp-oauth.keys.json")

    # Database Configuration
    alembic_database_url: str = Field(default="sqlite:///data/admonish.db")

    # Slack Configuration
    slack_app_token: str = Field(default="your_slack_app_token_here")

    # Calendar Configuration
    calendar_webhook_secret: str = Field(default="your_webhook_secret_here")
    calendar_watch_port: str = Field(default="8080")
    calendar_watch_host: str = Field(default="0.0.0.0")

    # Scheduler Configuration
    scheduler_timezone: str = Field(default="UTC")

    # Development Configuration
    debug: str = Field(default="true")
    log_level: str = Field(default="INFO")
    environment: str = Field(default="development")
    development: str = Field(default="true")

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
# TODO: should automatically read the environment variables/.env
