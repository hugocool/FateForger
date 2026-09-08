"""Tests for the Pydantic AutoGen event models.

Validates that dict payloads from autogen_core.events records are correctly
parsed into typed models, enabling clean dispatch without dict.get() chains.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# parse_autogen_event — happy paths
# ---------------------------------------------------------------------------

class TestParseAutogenEventDispatch:
    def test_llm_call_event_parsed(self):
        from fateforger.core.autogen_event_models import LLMEventPayload, parse_autogen_event
        ev = parse_autogen_event({
            "type": "LLMCall",
            "agent_id": "planner",
            "model": "gpt-4o",
            "stage": "refine",
            "session_key": "sess-1",
            "thread_ts": "1234.5",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "response": {"model": "gpt-4o", "usage": {}, "choices": []},
        })
        assert isinstance(ev, LLMEventPayload)
        assert ev.agent_id == "planner"
        assert ev.model == "gpt-4o"
        assert ev.prompt_tokens == 100
        assert ev.completion_tokens == 50

    def test_llm_stream_end_parsed(self):
        from fateforger.core.autogen_event_models import LLMEventPayload, parse_autogen_event
        ev = parse_autogen_event({"type": "LLMStreamEnd", "agent_id": "x"})
        assert isinstance(ev, LLMEventPayload)

    def test_tool_call_event_parsed(self):
        from fateforger.core.autogen_event_models import ToolCallPayload, parse_autogen_event
        ev = parse_autogen_event({"type": "ToolCall", "agent_id": "bg", "tool_name": "list-events"})
        assert isinstance(ev, ToolCallPayload)
        assert ev.tool_name == "list-events"
        assert ev.agent_id == "bg"

    def test_message_handler_exception_parsed(self):
        from fateforger.core.autogen_event_models import ExceptionPayload, parse_autogen_event
        ev = parse_autogen_event({
            "type": "MessageHandlerException",
            "agent_id": "x",
            "handling_agent": "handler",
            "error_type": "ValueError",
            "exception": "boom",
        })
        assert isinstance(ev, ExceptionPayload)
        assert ev.error_type == "ValueError"
        assert ev.component == "handler"  # falls back to handling_agent

    def test_agent_construction_exception_falls_back_to_agent_id(self):
        from fateforger.core.autogen_event_models import ExceptionPayload, parse_autogen_event
        ev = parse_autogen_event({
            "type": "AgentConstructionException",
            "agent_id": "my_agent",
        })
        assert isinstance(ev, ExceptionPayload)
        assert ev.component == "my_agent"

    def test_message_event_parsed(self):
        from fateforger.core.autogen_event_models import MessageEventPayload, parse_autogen_event
        ev = parse_autogen_event({
            "type": "Message",
            "sender": "planner/default",
            "receiver": None,
            "kind": "MessageKind.RESPOND",
            "delivery_stage": "DeliveryStage.SEND",
            "payload": '{"type": "TextMessage", "content": "hello"}',
        })
        assert isinstance(ev, MessageEventPayload)
        assert ev.sender == "planner/default"

    def test_unknown_type_returns_none(self):
        from fateforger.core.autogen_event_models import parse_autogen_event
        ev = parse_autogen_event({"type": "SomeUnknownEvent", "foo": "bar"})
        assert ev is None

    def test_missing_type_returns_none(self):
        from fateforger.core.autogen_event_models import parse_autogen_event
        ev = parse_autogen_event({"agent_id": "x"})
        assert ev is None

    def test_non_dict_returns_none(self):
        from fateforger.core.autogen_event_models import parse_autogen_event
        assert parse_autogen_event("not a dict") is None  # type: ignore[arg-type]
        assert parse_autogen_event(None) is None  # type: ignore[arg-type]
        assert parse_autogen_event([]) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LLMEventPayload — field coercion and convenience properties
# ---------------------------------------------------------------------------

class TestLLMEventPayload:
    def test_response_status_ok(self):
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall", response={"model": "gpt-4o"})
        assert ev.response_status == "ok"

    def test_response_status_error_when_response_error_field(self):
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall", response={"error": "timeout"})
        assert ev.response_status == "error"

    def test_response_status_error_on_empty_string_is_ok(self):
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall", response={"error": ""})
        assert ev.response_status == "ok"

    def test_response_model_extracted(self):
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall", response={"model": "gpt-4o-mini"})
        assert ev.response_model == "gpt-4o-mini"

    def test_response_model_falls_back_to_model_field(self):
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall", model="gpt-4o", response={})
        assert ev.response_model == "gpt-4o"

    def test_response_model_unknown_when_absent(self):
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall")
        assert ev.response_model == "unknown"

    def test_extra_fields_allowed(self):
        """Extra keys in the payload must not raise."""
        from fateforger.core.autogen_event_models import LLMEventPayload
        ev = LLMEventPayload(type="LLMCall", unexpected_key="value")  # type: ignore[call-arg]
        assert ev is not None


# ---------------------------------------------------------------------------
# ExceptionPayload — component resolution
# ---------------------------------------------------------------------------

class TestExceptionPayload:
    def test_handling_agent_takes_priority_over_agent_id(self):
        from fateforger.core.autogen_event_models import ExceptionPayload
        ev = ExceptionPayload(
            type="MessageHandlerException",
            agent_id="agent_a",
            handling_agent="handler_b",
        )
        assert ev.component == "handler_b"

    def test_agent_id_used_when_no_handling_agent(self):
        from fateforger.core.autogen_event_models import ExceptionPayload
        ev = ExceptionPayload(type="MessageHandlerException", agent_id="agent_a")
        assert ev.component == "agent_a"

    def test_component_unknown_when_both_absent(self):
        from fateforger.core.autogen_event_models import ExceptionPayload
        ev = ExceptionPayload(type="MessageHandlerException")
        assert ev.component == "unknown"


# ---------------------------------------------------------------------------
# MessageEventPayload — parsed_payload helper
# ---------------------------------------------------------------------------

class TestMessageEventPayload:
    def test_parsed_payload_decodes_json_string(self):
        import json
        from fateforger.core.autogen_event_models import MessageEventPayload
        inner = {"type": "TextMessage", "content": "hello", "source": "user"}
        ev = MessageEventPayload(
            type="Message",
            payload=json.dumps(inner),
        )
        assert ev.parsed_payload == inner

    def test_parsed_payload_none_when_missing(self):
        from fateforger.core.autogen_event_models import MessageEventPayload
        ev = MessageEventPayload(type="Message")
        assert ev.parsed_payload is None

    def test_parsed_payload_none_on_invalid_json(self):
        from fateforger.core.autogen_event_models import MessageEventPayload
        ev = MessageEventPayload(type="Message", payload="{broken}")
        assert ev.parsed_payload is None


# ---------------------------------------------------------------------------
# LLMResponse sub-model — typed access replacing isinstance(response, dict)
# ---------------------------------------------------------------------------


class TestLLMResponse:
    """LLMResponse + sub-models parse the raw response dict into typed fields."""

    def _make(self, **kwargs):
        from fateforger.core.autogen_event_models import LLMResponse
        return LLMResponse.model_validate(kwargs)

    def test_model_field(self) -> None:
        r = self._make(model="gpt-4o")
        assert r.model == "gpt-4o"

    def test_error_field(self) -> None:
        r = self._make(error="timeout")
        assert r.error == "timeout"

    def test_usage_parsed(self) -> None:
        r = self._make(usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        assert r.usage is not None
        assert r.usage.prompt_tokens == 10
        assert r.usage.completion_tokens == 5
        assert r.usage.total_tokens == 15

    def test_usage_none_when_absent(self) -> None:
        r = self._make(model="x")
        assert r.usage is None

    def test_finish_reason_via_first_choice(self) -> None:
        r = self._make(choices=[{"finish_reason": "stop", "message": {}}])
        assert r.finish_reason == "stop"

    def test_finish_reason_none_when_no_choices(self) -> None:
        r = self._make()
        assert r.finish_reason is None

    def test_tool_call_names_extracted(self) -> None:
        r = self._make(choices=[{
            "finish_reason": "tool_calls",
            "message": {
                "tool_calls": [
                    {"function": {"name": "list-events"}},
                    {"function": {"name": "get-event"}},
                ]
            },
        }])
        assert r.tool_call_names == ["list-events", "get-event"]

    def test_tool_call_names_empty_when_no_choices(self) -> None:
        r = self._make()
        assert r.tool_call_names == []

    def test_empty_dict_gives_defaults(self) -> None:
        from fateforger.core.autogen_event_models import LLMResponse
        r = LLMResponse.model_validate({})
        assert r.model is None
        assert r.usage is None
        assert r.choices == []
        assert r.finish_reason is None
        assert r.tool_call_names == []

    def test_extra_fields_allowed(self) -> None:
        r = self._make(model="x", unknown_future_field="y")
        assert r.model == "x"


class TestLLMEventPayloadResponseObj:
    """LLMEventPayload.response_obj surfaces the typed LLMResponse sub-model."""

    def _ev(self, response=None, **kw):
        from fateforger.core.autogen_event_models import LLMEventPayload
        return LLMEventPayload(type="LLMCall", response=response, **kw)

    def test_response_obj_none_when_no_response(self) -> None:
        assert self._ev().response_obj is None

    def test_response_obj_parsed(self) -> None:
        from fateforger.core.autogen_event_models import LLMResponse
        ev = self._ev(response={"model": "gpt-4o", "usage": {"prompt_tokens": 8}})
        assert isinstance(ev.response_obj, LLMResponse)
        assert ev.response_obj.model == "gpt-4o"
        assert ev.response_obj.usage is not None
        assert ev.response_obj.usage.prompt_tokens == 8

    def test_response_error_via_obj(self) -> None:
        ev = self._ev(response={"error": "server_error"})
        assert ev.response_error == "server_error"
        assert ev.response_status == "error"

    def test_response_model_via_obj(self) -> None:
        ev = self._ev(response={"model": "gpt-4o-mini"})
        assert ev.response_model == "gpt-4o-mini"

    def test_prompt_tokens_from_response_obj(self) -> None:
        ev = self._ev(response={"usage": {"prompt_tokens": 42, "completion_tokens": 7}})
        assert ev.prompt_tokens_from_response() == 42
        assert ev.completion_tokens_from_response() == 7

    def test_explicit_fields_take_priority_over_response(self) -> None:
        ev = self._ev(
            prompt_tokens=99,
            completion_tokens=11,
            response={"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        )
        assert ev.prompt_tokens_from_response() == 99
        assert ev.completion_tokens_from_response() == 11

