from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Mapping

from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import ModelFamily
from langchain_openai import ChatOpenAI

from fateforger.core.config import settings

ReasoningEffort = Literal["low", "medium", "high"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAICompatibleProviderConfig:
    api_key: str
    base_url: str | None
    default_headers: dict[str, str] | None


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _openai_compatible_provider_config(
    *, reasoning_effort: ReasoningEffort | None = None
) -> OpenAICompatibleProviderConfig:
    provider = _clean(settings.llm_provider).lower() or "openai"
    if provider == "openrouter":
        api_key = _clean(settings.openrouter_api_key)
        # Back-compat / convenience: if the user put their OpenRouter key into OPENAI_API_KEY,
        # accept it as a fallback (this is common in OpenAI-compatible stacks).
        if not api_key:
            openai_key = _clean(settings.openai_api_key)
            if openai_key and openai_key != "x":
                logger.warning(
                    "OPENROUTER_API_KEY is not set; falling back to OPENAI_API_KEY for OpenRouter auth"
                )
                api_key = openai_key
        if not api_key:
            raise RuntimeError(
                "LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set. "
                "Add OPENROUTER_API_KEY to `.env` (or set OPENAI_API_KEY to your OpenRouter key)."
            )
        base_url = _clean(settings.openrouter_base_url) or "https://openrouter.ai/api/v1"
        headers: dict[str, str] = {}
        if _clean(settings.openrouter_http_referer):
            headers["HTTP-Referer"] = _clean(settings.openrouter_http_referer)
        if _clean(settings.openrouter_title):
            headers["X-Title"] = _clean(settings.openrouter_title)
        if reasoning_effort and bool(getattr(settings, "openrouter_send_reasoning_effort_header", False)):
            header_name = _clean(settings.openrouter_reasoning_effort_header)
            if header_name:
                headers[header_name] = reasoning_effort
        return OpenAICompatibleProviderConfig(
            api_key=api_key, base_url=base_url, default_headers=headers or None
        )

    base_url = _clean(settings.openai_base_url) or None
    return OpenAICompatibleProviderConfig(
        api_key=_clean(settings.openai_api_key), base_url=base_url, default_headers=None
    )


def _model_for_agent(agent_type: str) -> str:
    agent_type = _clean(agent_type)
    provider = _clean(settings.llm_provider).lower() or "openai"

    # Provider-specific defaults; override via `LLM_MODEL_*` env vars.
    openai_default = _clean(settings.openai_model) or "gpt-4o-mini"
    openrouter_flash = _clean(getattr(settings, "openrouter_default_model_flash", "")) or "google/gemini-2.0-flash-001"
    openrouter_pro = _clean(getattr(settings, "openrouter_default_model_pro", "")) or "google/gemini-3-flash-preview"

    def pick(override: str, *, openai: str, openrouter: str) -> str:
        override = _clean(override)
        if override:
            return override
        return openrouter if provider == "openrouter" else openai

    if agent_type == "receptionist_agent":
        return pick(
            settings.llm_model_receptionist, openai=openai_default, openrouter=openrouter_flash
        )
    if agent_type == "planner_agent":
        return pick(
            settings.llm_model_planner, openai="gpt-4o", openrouter=openrouter_flash
        )
    if agent_type == "timeboxing_agent":
        return pick(
            settings.llm_model_timeboxing, openai=openai_default, openrouter=openrouter_flash
        )
    if agent_type == "timeboxing_draft":
        return pick(
            settings.llm_model_timeboxing_draft, openai="gpt-4o", openrouter=openrouter_pro
        )
    if agent_type == "revisor_agent":
        return pick(settings.llm_model_revisor, openai="gpt-4o", openrouter=openrouter_pro)
    if agent_type == "tasks_agent":
        return pick(
            settings.llm_model_tasks, openai=openai_default, openrouter=openrouter_pro
        )
    if agent_type == "calendar_submitter":
        return pick(
            settings.llm_model_calendar_submitter,
            openai=openai_default,
            openrouter=openrouter_flash,
        )
    if agent_type == "timebox_patcher":
        return pick(
            settings.llm_model_timebox_patcher,
            openai=openai_default,
            openrouter=openrouter_pro,
        )
    if agent_type == "admonisher_agent":
        return pick(
            settings.llm_model_admonisher, openai=openai_default, openrouter=openrouter_flash
        )

    return openai_default if provider != "openrouter" else openrouter_flash


def _reasoning_effort_for_agent(agent_type: str) -> ReasoningEffort | None:
    provider = _clean(settings.llm_provider).lower() or "openai"
    if provider != "openrouter":
        return None

    agent_type = _clean(agent_type)

    def normalize(value: str) -> ReasoningEffort | None:
        value = _clean(value).lower()
        if value in ("low", "medium", "high"):
            return value  # type: ignore[return-value]
        return None

    if agent_type == "timeboxing_agent":
        return normalize(settings.llm_reasoning_effort_timeboxing) or "high"
    if agent_type == "timeboxing_draft":
        return normalize(settings.llm_reasoning_effort_timeboxing_draft) or "high"
    if agent_type == "revisor_agent":
        return normalize(settings.llm_reasoning_effort_revisor) or "medium"
    if agent_type == "tasks_agent":
        return normalize(settings.llm_reasoning_effort_tasks) or "medium"
    if agent_type == "timebox_patcher":
        return normalize(settings.llm_reasoning_effort_timebox_patcher) or "high"
    return None


def _ensure_autogen_ext_allows_openai_sdk_passthrough_args() -> None:
    """Allow `extra_body` / `extra_headers` passthrough to the OpenAI SDK.

    AutoGen's `OpenAIChatCompletionClient` validates args against OpenAI's typed
    ChatCompletion params, but OpenAI SDK supports `extra_body`/`extra_headers`
    as generic passthrough args. We add them to the allowlist so we can send
    OpenRouter-only parameters like `reasoning.effort` in the request body.
    """

    try:
        import autogen_ext.models.openai._openai_client as oc
    except Exception:
        return
    oc.create_kwargs.add("extra_body")
    oc.create_kwargs.add("extra_headers")


def build_autogen_chat_client(
    agent_type: str,
    *,
    model: str | None = None,
    parallel_tool_calls: bool | None = None,
    temperature: float | None = None,
) -> OpenAIChatCompletionClient:
    resolved_model = _clean(model) or _model_for_agent(agent_type)
    reasoning_effort = _reasoning_effort_for_agent(agent_type)
    provider = _openai_compatible_provider_config(reasoning_effort=reasoning_effort)

    _ensure_autogen_ext_allows_openai_sdk_passthrough_args()

    if temperature is None and _clean(agent_type) == "admonisher_agent":
        temperature = float(getattr(settings, "llm_temperature_admonisher", 1.1))

    kwargs: dict = {
        "model": resolved_model,
        "api_key": provider.api_key,
    }
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    if provider.default_headers:
        kwargs["default_headers"] = provider.default_headers
    if parallel_tool_calls is not None:
        kwargs["parallel_tool_calls"] = parallel_tool_calls
    if temperature is not None:
        kwargs["temperature"] = temperature
    if _clean(settings.llm_provider).lower() == "openrouter" and reasoning_effort:
        kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort}}
    if _clean(settings.llm_provider).lower() == "openrouter":
        # OpenRouter model IDs (e.g. `google/gemini-...`) are not valid OpenAI model names,
        # so autogen-ext requires an explicit ModelInfo.
        kwargs["model_info"] = {
            "family": ModelFamily.ANY,
            "vision": False,
            "function_calling": True,
            "json_output": True,
            "structured_output": True,
            "multiple_system_messages": True,
        }

    return OpenAIChatCompletionClient(**kwargs)


def build_langchain_chat_openai(
    agent_type: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    extra_headers: Mapping[str, str] | None = None,
) -> ChatOpenAI:
    resolved_model = _clean(model) or _model_for_agent(agent_type)
    reasoning_effort = _reasoning_effort_for_agent(agent_type)
    provider = _openai_compatible_provider_config(reasoning_effort=reasoning_effort)

    headers = dict(provider.default_headers or {})
    if extra_headers:
        headers.update({str(k): str(v) for k, v in extra_headers.items()})

    kwargs: dict = {
        "model": resolved_model,
        "temperature": temperature,
        "api_key": provider.api_key,
    }
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    if headers:
        kwargs["default_headers"] = headers
    if _clean(settings.llm_provider).lower() == "openrouter" and reasoning_effort:
        kwargs["extra_body"] = {"reasoning": {"effort": reasoning_effort}}
    return ChatOpenAI(**kwargs)


__all__ = ["build_autogen_chat_client", "build_langchain_chat_openai", "ReasoningEffort"]
