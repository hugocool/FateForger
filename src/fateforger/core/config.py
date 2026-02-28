import os
import sys
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration using environment variables."""

    model_config = SettingsConfigDict(
        env_file=(
            None
            if ("pytest" in sys.modules) or os.getenv("PYTEST_CURRENT_TEST")
            else os.getenv("FATEFORGER_ENV_FILE", ".env")
        ),
        case_sensitive=False,
        extra="ignore",
    )

    # Core Configuration
    slack_bot_token: str = Field(default="x")
    slack_user_token: str = Field(default="")
    slack_test_user_token: str = Field(default="")
    slack_signing_secret: str = Field(default="x")
    # Slack Configuration
    slack_app_token: str = Field(default="your_slack_app_token_here")
    slack_socket_mode: bool = Field(default=True)
    slack_port: int = Field(default=3000)
    slack_focus_ttl_seconds: int = Field(
        default=60 * 60
    )
    slack_app_name: str = Field(default="FateForger")
    slack_timeboxing_channel_id: str = Field(
        default=""
    )
    slack_strategy_channel_id: str = Field(default="")
    slack_tasks_channel_id: str = Field(default="")
    slack_ops_channel_id: str = Field(default="")
    slack_general_channel_id: str = Field(default="")
    slack_agent_icon_base_url: str = Field(
        default="https://raw.githubusercontent.com/hugocool/FateForger/e18b991b25c7acc5fd759e94680ac8f70bc1e830/docs/agent_icons",
    )

    openai_api_key: str = Field(default="x")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_base_url: str = Field(default="")
    gemini_api_key: str = Field(default="x")
    database_url: str = Field(default="sqlite:///:memory:")

    # LLM Provider Configuration
    # - "openai": OpenAI-hosted models via https://api.openai.com/v1
    # - "openrouter": OpenRouter OpenAI-compatible endpoint via https://openrouter.ai/api/v1
    llm_provider: str = Field(default="openai")
    openrouter_api_key: str = Field(default="")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1"
    )
    openrouter_http_referer: str = Field(default="")
    openrouter_title: str = Field(default="FateForger")
    openrouter_send_reasoning_effort_header: bool = Field(
        default=False
    )
    openrouter_reasoning_effort_header: str = Field(
        default="X-Reasoning-Effort"
    )
    openrouter_default_model_flash: str = Field(
        default="google/gemini-3-flash-preview"
    )
    openrouter_default_model_pro: str = Field(
        default="google/gemini-3-flash-preview"
    )

    # Per-agent model selection (optional; provider-specific model IDs)
    llm_model_receptionist: str = Field(default="")
    llm_model_admonisher: str = Field(default="")
    llm_model_timeboxing: str = Field(default="")
    llm_model_timeboxing_draft: str = Field(
        default=""
    )
    llm_model_timebox_patcher: str = Field(
        default=""
    )
    llm_model_planner: str = Field(default="")
    llm_model_revisor: str = Field(default="")
    llm_model_tasks: str = Field(default="")
    llm_model_calendar_submitter: str = Field(
        default=""
    )

    # Per-agent temperature
    llm_temperature_admonisher: float = Field(
        default=1.1
    )

    # Per-agent reasoning effort (OpenRouter: request body `reasoning.effort`; "low"|"medium"|"high")
    llm_reasoning_effort_timeboxing: str = Field(
        default=""
    )
    llm_reasoning_effort_timeboxing_draft: str = Field(
        default=""
    )
    llm_reasoning_effort_revisor: str = Field(
        default=""
    )
    llm_reasoning_effort_tasks: str = Field(
        default=""
    )
    llm_reasoning_effort_timebox_patcher: str = Field(
        default=""
    )
    llm_max_tokens: int = Field(default=0)
    llm_max_tokens_timebox_patcher: int = Field(
        default=0
    )

    # MCP Server Configuration
    mcp_version: str = Field(default="v1.4.8")
    port: str = Field(default="3000")
    transport: str = Field(default="stdio")
    google_oauth_credentials: str = Field(default="/app/gcp-oauth.keys.json")
    mcp_http_port: str = Field(default="3001")
    mcp_http_auth_token: str = Field(default="change_me_to_a_long_random_secret")
    mcp_calendar_server_url: str = Field(
        default="http://localhost:3000"
    )
    mcp_calendar_server_url_docker: str = Field(
        default="http://calendar-mcp:3000"
    )
    notion_mcp_url: str = Field(
        default="http://localhost:3001/mcp"
    )
    ticktick_mcp_url: str = Field(
        default="http://ticktick-mcp:8000/mcp"
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
    work_notion_token: str = Field(default="")
    notion_timeboxing_parent_page_id: str = Field(
        default=""
    )
    notion_sprint_db_id: str = Field(default="")
    notion_sprint_data_source_url: str = Field(
        default=""
    )
    notion_sprint_db_ids: str = Field(default="")
    notion_sprint_data_source_urls: str = Field(
        default=""
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

    wizard_admin_token: str = Field(default="admin_token")
    wizard_session_secret: str = Field(default="")

    # Agent runtime timeouts (seconds)
    agent_on_messages_timeout_seconds: int = Field(
        default=60
    )
    agent_mcp_discovery_timeout_seconds: int = Field(
        default=10
    )
    slack_register_user_timeout_seconds: float = Field(default=3.0, gt=0.0)
    slack_route_dispatch_timeout_seconds: float = Field(default=75.0, gt=0.0)

    # Timeboxing feature flags
    timeboxing_memory_backend: str = Field(
        default="constraint_mcp"
    )

    # Mem0 Memory Configuration
    mem0_user_id: str = Field(default="timeboxing")
    mem0_api_key: str = Field(default="")
    mem0_is_cloud: bool = Field(default=False)
    mem0_local_config_json: str = Field(default="")
    mem0_query_limit: int = Field(default=200)

    # Observability Configuration
    obs_prometheus_enabled: bool = Field(default=True)
    obs_prometheus_port: int = Field(default=9464)
    obs_llm_audit_enabled: bool = Field(default=True)
    obs_llm_audit_sink: str = Field(default="loki")
    obs_llm_audit_mode: str = Field(default="sanitized")
    obs_llm_audit_max_chars: int = Field(default=2000)
    obs_llm_audit_loki_url: str = Field(default="http://localhost:3100/loki/api/v1/push")
    obs_llm_audit_queue_max: int = Field(default=4096, ge=1, le=200000)
    obs_llm_audit_batch_size: int = Field(default=128, ge=1, le=10000)
    obs_llm_audit_flush_interval_ms: int = Field(default=250, ge=10, le=60000)
    autogen_events_log: str = Field(default="summary")
    autogen_events_output_target: str = Field(default="stdout")
    autogen_events_full_payload_mode: str = Field(default="sanitized")

    @field_validator("slack_user_token", "slack_test_user_token")
    @classmethod
    def _validate_slack_user_token(cls, value: str) -> str:
        token = (value or "").strip()
        if not token:
            return ""
        if token.startswith("xoxp-"):
            return token
        raise ValueError(
            "SLACK_USER_TOKEN and SLACK_TEST_USER_TOKEN must start with 'xoxp-' when provided"
        )

    @field_validator("mcp_calendar_server_url", "mcp_calendar_server_url_docker")
    @classmethod
    def _validate_mcp_calendar_url(cls, value: str) -> str:
        parsed = urlparse((value or "").strip())
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
        raise ValueError(
            "MCP calendar URL must be absolute and start with http:// or https://"
        )

    @field_validator("notion_mcp_url", "ticktick_mcp_url")
    @classmethod
    def _validate_mcp_tool_endpoint_url(cls, value: str) -> str:
        parsed = urlparse((value or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                "MCP endpoint URL must be absolute and start with http:// or https://"
            )
        if not parsed.netloc:
            raise ValueError("MCP endpoint URL must include a host")
        if not parsed.path or parsed.path == "/":
            raise ValueError("MCP endpoint URL must include explicit path (e.g. /mcp)")
        return value

    @field_validator("llm_max_tokens", "llm_max_tokens_timebox_patcher")
    @classmethod
    def _validate_non_negative_tokens(cls, value: int) -> int:
        if value >= 0:
            return value
        raise ValueError("LLM max token limits must be >= 0")

    @field_validator("timeboxing_memory_backend")
    @classmethod
    def _validate_timeboxing_memory_backend(cls, value: str) -> str:
        backend = (value or "").strip().lower()
        if backend in {"constraint_mcp", "mem0"}:
            return backend
        raise ValueError(
            "TIMEBOXING_MEMORY_BACKEND must be one of: constraint_mcp, mem0"
        )

    @field_validator("autogen_events_log")
    @classmethod
    def _validate_autogen_events_log(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        alias = {
            "0": "off",
            "false": "off",
            "none": "off",
            "1": "full",
            "true": "full",
            "on": "full",
        }
        normalized = alias.get(mode, mode)
        if normalized in {"summary", "full", "off"}:
            return normalized
        raise ValueError("AUTOGEN_EVENTS_LOG must be one of: summary, full, off")

    @field_validator("autogen_events_output_target")
    @classmethod
    def _validate_autogen_events_output_target(cls, value: str) -> str:
        target = (value or "").strip().lower()
        if target in {"stdout", "audit"}:
            return target
        raise ValueError(
            "AUTOGEN_EVENTS_OUTPUT_TARGET must be one of: stdout, audit"
        )

    @field_validator("autogen_events_full_payload_mode")
    @classmethod
    def _validate_autogen_events_full_payload_mode(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        if mode in {"sanitized", "raw"}:
            return mode
        raise ValueError(
            "AUTOGEN_EVENTS_FULL_PAYLOAD_MODE must be one of: sanitized, raw"
        )

    @field_validator("obs_llm_audit_sink")
    @classmethod
    def _validate_obs_llm_audit_sink(cls, value: str) -> str:
        sink = (value or "").strip().lower()
        if sink in {"off", "loki", "file", "both"}:
            return sink
        raise ValueError("OBS_LLM_AUDIT_SINK must be one of: off, loki, file, both")

    @field_validator("obs_llm_audit_mode")
    @classmethod
    def _validate_obs_llm_audit_mode(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        if mode in {"off", "sanitized", "raw"}:
            return mode
        raise ValueError("OBS_LLM_AUDIT_MODE must be one of: off, sanitized, raw")

    @field_validator("obs_llm_audit_loki_url")
    @classmethod
    def _validate_obs_llm_audit_loki_url(cls, value: str) -> str:
        parsed = urlparse((value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "OBS_LLM_AUDIT_LOKI_URL must be absolute and start with http:// or https://"
            )
        if not parsed.path or parsed.path == "/":
            raise ValueError(
                "OBS_LLM_AUDIT_LOKI_URL must include explicit push path (e.g. /loki/api/v1/push)"
            )
        return value

    @model_validator(mode="after")
    def _validate_runtime_invariants(self) -> "Settings":
        if not self.slack_socket_mode:
            raise ValueError("SLACK_SOCKET_MODE must remain enabled")
        if self.timeboxing_memory_backend == "mem0":
            local_config = (self.mem0_local_config_json or "").strip()
            has_local_runtime = bool(local_config)
            has_cloud_runtime = bool(self.mem0_is_cloud and self.mem0_api_key.strip())
            if not (has_local_runtime or has_cloud_runtime):
                raise ValueError(
                    "Mem0 backend requires MEM0_LOCAL_CONFIG_JSON or "
                    "(MEM0_IS_CLOUD=1 and MEM0_API_KEY)."
                )
        return self


settings = Settings()
