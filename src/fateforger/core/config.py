from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration using environment variables."""

    # Core Configuration
    slack_bot_token: str = Field(default="x", env="SLACK_BOT_TOKEN")
    slack_signing_secret: str = Field(default="x")
    # Slack Configuration
    slack_app_token: str = Field(default="your_slack_app_token_here")
    slack_socket_mode: bool = Field(default=True, env="SLACK_SOCKET_MODE")
    slack_port: int = Field(default=3000, env="SLACK_PORT")
    slack_focus_ttl_seconds: int = Field(default=60 * 60, env="SLACK_FOCUS_TTL_SECONDS")
    slack_app_name: str = Field(default="FateForger")

    openai_api_key: str = Field(default="x")
    gemini_api_key: str = Field(default="x")
    database_url: str = Field(default="sqlite:///:memory:")

    # MCP Server Configuration
    mcp_version: str = Field(default="v1.4.8")
    port: str = Field(default="3000")
    transport: str = Field(default="stdio")
    google_oauth_credentials: str = Field(default="/app/gcp-oauth.keys.json")
    mcp_http_port: str = Field(default="3001")
    mcp_http_auth_token: str = Field(default="change_me_to_a_long_random_secret")

    # TickTick Configuration
    ticktick_mcp_version: str = Field(default="main")
    ticktick_server_transport: str = Field(default="streamable-http")
    ticktick_username: str = Field(default="")
    ticktick_password: str = Field(default="your_ticktick_password")
    ticktick_client_id: str = Field(default="")
    ticktick_client_secret: str = Field(default="")
    ticktick_access_token: str = Field(default="")

    # Notion Configuration
    notion_token: str = Field(default="")

    # Database Configuration
    alembic_database_url: str = Field(default="sqlite:///data/admonish.db")

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
