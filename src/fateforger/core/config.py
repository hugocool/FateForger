import os
import sys
from urllib.parse import urlparse
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration using environment variables."""

    # Core Configuration
    slack_bot_token: str = Field(default="x", env="SLACK_BOT_TOKEN")
    slack_user_token: str = Field(default="", env="SLACK_USER_TOKEN")
    slack_test_user_token: str = Field(default="", env="SLACK_TEST_USER_TOKEN")
    slack_signing_secret: str = Field(default="x")
    # Slack Configuration
    slack_app_token: str = Field(default="your_slack_app_token_here")
    slack_socket_mode: bool = Field(default=True, env="SLACK_SOCKET_MODE")
    slack_port: int = Field(default=3000, env="SLACK_PORT")
    slack_focus_ttl_seconds: int = Field(default=60 * 60, env="SLACK_FOCUS_TTL_SECONDS")
    slack_app_name: str = Field(default="FateForger")
    slack_timeboxing_channel_id: str = Field(
        default="", env="SLACK_TIMEBOXING_CHANNEL_ID"
    )
    slack_strategy_channel_id: str = Field(default="", env="SLACK_STRATEGY_CHANNEL_ID")
    slack_tasks_channel_id: str = Field(default="", env="SLACK_TASKS_CHANNEL_ID")
    slack_ops_channel_id: str = Field(default="", env="SLACK_OPS_CHANNEL_ID")
    slack_general_channel_id: str = Field(default="", env="SLACK_GENERAL_CHANNEL_ID")
    slack_agent_icon_base_url: str = Field(
        default="https://raw.githubusercontent.com/hugocool/FateForger/e18b991b25c7acc5fd759e94680ac8f70bc1e830/docs/agent_icons",
        env="SLACK_AGENT_ICON_BASE_URL",
    )

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
    openrouter_default_model_flash: str = Field(
        default="google/gemini-3-flash-preview", env="OPENROUTER_DEFAULT_MODEL_FLASH"
    )
    openrouter_default_model_pro: str = Field(
        default="google/gemini-3-flash-preview", env="OPENROUTER_DEFAULT_MODEL_PRO"
    )

    # Per-agent model selection (optional; provider-specific model IDs)
    llm_model_receptionist: str = Field(default="", env="LLM_MODEL_RECEPTIONIST")
    llm_model_admonisher: str = Field(default="", env="LLM_MODEL_ADMONISHER")
    llm_model_timeboxing: str = Field(default="", env="LLM_MODEL_TIMEBOXING")
    llm_model_timeboxing_draft: str = Field(
        default="", env="LLM_MODEL_TIMEBOXING_DRAFT"
    )
    llm_model_timebox_patcher: str = Field(default="", env="LLM_MODEL_TIMEBOX_PATCHER")
    llm_model_planner: str = Field(default="", env="LLM_MODEL_PLANNER")
    llm_model_revisor: str = Field(default="", env="LLM_MODEL_REVISOR")
    llm_model_tasks: str = Field(default="", env="LLM_MODEL_TASKS")
    llm_model_calendar_submitter: str = Field(
        default="", env="LLM_MODEL_CALENDAR_SUBMITTER"
    )

    # Per-agent temperature
    llm_temperature_admonisher: float = Field(
        default=1.1, env="LLM_TEMPERATURE_ADMONISHER"
    )

    # Per-agent reasoning effort (OpenRouter: request body `reasoning.effort`; "low"|"medium"|"high")
    llm_reasoning_effort_timeboxing: str = Field(
        default="", env="LLM_REASONING_EFFORT_TIMEBOXING"
    )
    llm_reasoning_effort_timeboxing_draft: str = Field(
        default="", env="LLM_REASONING_EFFORT_TIMEBOXING_DRAFT"
    )
    llm_reasoning_effort_revisor: str = Field(
        default="", env="LLM_REASONING_EFFORT_REVISOR"
    )
    llm_reasoning_effort_tasks: str = Field(
        default="", env="LLM_REASONING_EFFORT_TASKS"
    )
    llm_reasoning_effort_timebox_patcher: str = Field(
        default="", env="LLM_REASONING_EFFORT_TIMEBOX_PATCHER"
    )
    llm_max_tokens: int = Field(default=0, env="LLM_MAX_TOKENS")
    llm_max_tokens_timebox_patcher: int = Field(
        default=0, env="LLM_MAX_TOKENS_TIMEBOX_PATCHER"
    )

    # MCP Server Configuration
    mcp_version: str = Field(default="v1.4.8")
    port: str = Field(default="3000")
    transport: str = Field(default="stdio")
    google_oauth_credentials: str = Field(default="/app/gcp-oauth.keys.json")
    mcp_http_port: str = Field(default="3001")
    mcp_http_auth_token: str = Field(default="change_me_to_a_long_random_secret")
    mcp_calendar_server_url: str = Field(
        default="http://localhost:3000", env="MCP_CALENDAR_SERVER_URL"
    )
    mcp_calendar_server_url_docker: str = Field(
        default="http://calendar-mcp:3000", env="MCP_CALENDAR_SERVER_URL_DOCKER"
    )

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
    work_notion_token: str = Field(default="", env="WORK_NOTION_TOKEN")
    notion_timeboxing_parent_page_id: str = Field(
        default="", env="NOTION_TIMEBOXING_PARENT_PAGE_ID"
    )
    notion_sprint_db_id: str = Field(default="", env="NOTION_SPRINT_DB_ID")
    notion_sprint_data_source_url: str = Field(
        default="", env="NOTION_SPRINT_DATA_SOURCE_URL"
    )
    notion_sprint_db_ids: str = Field(default="", env="NOTION_SPRINT_DB_IDS")
    notion_sprint_data_source_urls: str = Field(
        default="", env="NOTION_SPRINT_DATA_SOURCE_URLS"
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

    wizard_admin_token: str = Field(default="admin_token", env="WIZARD_ADMIN_TOKEN")
    wizard_session_secret: str = Field(default="", env="WIZARD_SESSION_SECRET")

    # Agent runtime timeouts (seconds)
    agent_on_messages_timeout_seconds: int = Field(
        default=60, env="AGENT_ON_MESSAGES_TIMEOUT_SECONDS"
    )
    agent_mcp_discovery_timeout_seconds: int = Field(
        default=10, env="AGENT_MCP_DISCOVERY_TIMEOUT_SECONDS"
    )

    # Timeboxing feature flags
    timeboxing_memory_backend: str = Field(
        default="mem0", env="TIMEBOXING_MEMORY_BACKEND"
    )

    # Mem0 Memory Configuration
    mem0_user_id: str = Field(default="timeboxing", env="MEM0_USER_ID")
    mem0_api_key: str = Field(default="", env="MEM0_API_KEY")
    mem0_is_cloud: bool = Field(default=False, env="MEM0_IS_CLOUD")
    mem0_local_config_json: str = Field(default="", env="MEM0_LOCAL_CONFIG_JSON")
    mem0_query_limit: int = Field(default=200, env="MEM0_QUERY_LIMIT")

    # Observability Configuration
    obs_prometheus_enabled: bool = Field(default=True, env="OBS_PROMETHEUS_ENABLED")
    obs_prometheus_port: int = Field(default=9464, env="OBS_PROMETHEUS_PORT")
    obs_llm_audit_enabled: bool = Field(default=True, env="OBS_LLM_AUDIT_ENABLED")
    obs_llm_audit_mode: str = Field(default="sanitized", env="OBS_LLM_AUDIT_MODE")
    obs_llm_audit_max_chars: int = Field(
        default=2000, env="OBS_LLM_AUDIT_MAX_CHARS"
    )

    class Config:
        env_file = (
            None
            if ("pytest" in sys.modules) or os.getenv("PYTEST_CURRENT_TEST")
            else os.getenv("FATEFORGER_ENV_FILE", ".env")
        )
        case_sensitive = False

    @field_validator("slack_user_token")
    @classmethod
    def _validate_slack_user_token(cls, value: str) -> str:
        token = (value or "").strip()
        if not token:
            return ""
        if token.startswith("xoxp-"):
            return token
        raise ValueError("SLACK_USER_TOKEN must start with 'xoxp-' when provided")

    @field_validator("mcp_calendar_server_url", "mcp_calendar_server_url_docker")
    @classmethod
    def _validate_mcp_calendar_url(cls, value: str) -> str:
        parsed = urlparse((value or "").strip())
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
        raise ValueError(
            "MCP calendar URL must be absolute and start with http:// or https://"
        )

    @field_validator("llm_max_tokens", "llm_max_tokens_timebox_patcher")
    @classmethod
    def _validate_non_negative_tokens(cls, value: int) -> int:
        if value >= 0:
            return value
        raise ValueError("LLM max token limits must be >= 0")

    @model_validator(mode="after")
    def _validate_runtime_invariants(self) -> "Settings":
        if not self.slack_socket_mode:
            raise ValueError("SLACK_SOCKET_MODE must remain enabled")
        return self


settings = Settings()
# TODO: should automatically read the environment variables/.env
