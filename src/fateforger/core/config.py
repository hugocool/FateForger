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
    slack_timeboxing_channel_id: str = Field(default="", env="SLACK_TIMEBOXING_CHANNEL_ID")
    slack_strategy_channel_id: str = Field(default="", env="SLACK_STRATEGY_CHANNEL_ID")
    slack_tasks_channel_id: str = Field(default="", env="SLACK_TASKS_CHANNEL_ID")
    slack_ops_channel_id: str = Field(default="", env="SLACK_OPS_CHANNEL_ID")
    slack_general_channel_id: str = Field(default="", env="SLACK_GENERAL_CHANNEL_ID")

    openai_api_key: str = Field(default="x")
    openai_model: str = Field(default="gpt-4o-mini", env="OPENAI_MODEL")
    openai_base_url: str = Field(default="", env="OPENAI_BASE_URL")
    gemini_api_key: str = Field(default="x")
    database_url: str = Field(default="sqlite:///:memory:")

    # LLM Provider Configuration
    # - "openai": OpenAI-hosted models via https://api.openai.com/v1
    # - "openrouter": OpenRouter OpenAI-compatible endpoint via https://openrouter.ai/api/v1
    llm_provider: str = Field(default="openai", env="LLM_PROVIDER")
    openrouter_api_key: str = Field(default="", env="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", env="OPENROUTER_BASE_URL"
    )
    openrouter_http_referer: str = Field(default="", env="OPENROUTER_HTTP_REFERER")
    openrouter_title: str = Field(default="FateForger", env="OPENROUTER_TITLE")
    openrouter_send_reasoning_effort_header: bool = Field(
        default=False, env="OPENROUTER_SEND_REASONING_EFFORT_HEADER"
    )
    openrouter_reasoning_effort_header: str = Field(
        default="X-Reasoning-Effort", env="OPENROUTER_REASONING_EFFORT_HEADER"
    )

    # Per-agent model selection (optional; provider-specific model IDs)
    llm_model_receptionist: str = Field(default="", env="LLM_MODEL_RECEPTIONIST")
    llm_model_admonisher: str = Field(default="", env="LLM_MODEL_ADMONISHER")
    llm_model_timeboxing: str = Field(default="", env="LLM_MODEL_TIMEBOXING")
    llm_model_timebox_patcher: str = Field(default="", env="LLM_MODEL_TIMEBOX_PATCHER")
    llm_model_planner: str = Field(default="", env="LLM_MODEL_PLANNER")
    llm_model_revisor: str = Field(default="", env="LLM_MODEL_REVISOR")
    llm_model_tasks: str = Field(default="", env="LLM_MODEL_TASKS")
    llm_model_calendar_submitter: str = Field(
        default="", env="LLM_MODEL_CALENDAR_SUBMITTER"
    )

    # Per-agent reasoning effort (OpenRouter: request body `reasoning.effort`; "low"|"medium"|"high")
    llm_reasoning_effort_timeboxing: str = Field(
        default="", env="LLM_REASONING_EFFORT_TIMEBOXING"
    )
    llm_reasoning_effort_revisor: str = Field(
        default="", env="LLM_REASONING_EFFORT_REVISOR"
    )
    llm_reasoning_effort_tasks: str = Field(
        default="", env="LLM_REASONING_EFFORT_TASKS"
    )

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
    notion_timeboxing_parent_page_id: str = Field(
        default="", env="NOTION_TIMEBOXING_PARENT_PAGE_ID"
    )

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
