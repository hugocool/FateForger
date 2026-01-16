import json

import pytest

from autogen_core.models import UserMessage

from fateforger.core.config import settings
from fateforger.llm import build_autogen_chat_client


@pytest.mark.asyncio
async def test_openrouter_reasoning_effort_sent_in_request_body(httpx_mock, monkeypatch):
    # Configure OpenRouter provider in-process (settings is a singleton).
    monkeypatch.setattr(settings, "llm_provider", "openrouter", raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key", raising=False)
    monkeypatch.setattr(
        settings, "openrouter_base_url", "https://openrouter.test/api/v1", raising=False
    )
    monkeypatch.setattr(
        settings, "openrouter_http_referer", "http://localhost/test", raising=False
    )
    monkeypatch.setattr(settings, "openrouter_title", "FateForger Test", raising=False)
    monkeypatch.setattr(
        settings, "openrouter_send_reasoning_effort_header", False, raising=False
    )
    monkeypatch.setattr(
        settings, "llm_reasoning_effort_timeboxing", "high", raising=False
    )

    httpx_mock.add_response(
        method="POST",
        url="https://openrouter.test/api/v1/chat/completions",
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )

    client = build_autogen_chat_client("timeboxing_agent", model="gpt-4o-mini")
    await client.create([UserMessage(content="hello", source="user")])

    req = httpx_mock.get_requests()[0]
    assert req.headers.get("authorization") == "Bearer test-key"
    assert req.headers.get("http-referer") == "http://localhost/test"
    assert req.headers.get("x-title") == "FateForger Test"

    payload = json.loads(req.content.decode("utf-8"))
    assert payload["model"] == "gpt-4o-mini"

    # OpenRouter reasoning control is sent via OpenAI SDK passthrough `extra_body`,
    # which should appear as a top-level `reasoning` object in the request body.
    assert payload["reasoning"]["effort"] == "high"


@pytest.mark.asyncio
async def test_openrouter_reasoning_effort_header_is_opt_in(httpx_mock, monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter", raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key", raising=False)
    monkeypatch.setattr(
        settings, "openrouter_base_url", "https://openrouter.test/api/v1", raising=False
    )
    monkeypatch.setattr(settings, "openrouter_send_reasoning_effort_header", True, raising=False)
    monkeypatch.setattr(settings, "openrouter_reasoning_effort_header", "X-Reasoning-Effort", raising=False)
    monkeypatch.setattr(settings, "llm_reasoning_effort_tasks", "medium", raising=False)

    httpx_mock.add_response(
        method="POST",
        url="https://openrouter.test/api/v1/chat/completions",
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )

    client = build_autogen_chat_client("tasks_agent", model="gpt-4o-mini")
    await client.create([UserMessage(content="hello", source="user")])

    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-reasoning-effort") == "medium"


def test_openrouter_non_openai_model_id_does_not_require_model_info(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openrouter", raising=False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key", raising=False)
    monkeypatch.setattr(
        settings, "openrouter_base_url", "https://openrouter.test/api/v1", raising=False
    )

    client = build_autogen_chat_client(
        "receptionist_agent", model="google/gemini-2.0-flash-001"
    )
    assert client is not None
