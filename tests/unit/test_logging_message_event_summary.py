"""Tests for _summarize_autogen_message_event and _summarize_autogen_event_message.

These cover the typed-dispatch path that uses MessageEventPayload.parsed_payload,
removing the need for an extra json.loads + isinstance(payload_obj, dict) chain.
"""

from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message_event(
    *,
    sender: str = "planner_agent/default",
    receiver: str | None = None,
    kind: str = "MessageKind.RESPOND",
    stage: str = "DeliveryStage.SEND",
    payload_obj: dict | None = None,
) -> dict:
    return {
        "type": "Message",
        "agent_id": sender,
        "sender": sender,
        "receiver": receiver,
        "kind": kind,
        "delivery_stage": stage,
        "payload": json.dumps(payload_obj) if payload_obj is not None else None,
    }


def _make_text_message_payload(
    content: str = "Hello world",
    source: str = "planner",
    msg_id: str = "abc123",
) -> dict:
    return {
        "type": "TextMessage",
        "id": msg_id,
        "source": source,
        "content": content,
    }


# ---------------------------------------------------------------------------
# Tests for _summarize_autogen_message_event (the inner helper)
# ---------------------------------------------------------------------------


class TestSummarizeAutogenMessageEvent:
    """_summarize_autogen_message_event dispatches via MessageEventPayload."""

    def _call(self, event_dict: dict, max_chars: int = 500) -> str:
        from fateforger.core.logging_config import _summarize_autogen_message_event
        from fateforger.core.autogen_event_models import MessageEventPayload

        model = MessageEventPayload.model_validate(event_dict)
        return _summarize_autogen_message_event(model, max_chars=max_chars)

    def test_basic_parts_present(self):
        event = _make_message_event(
            sender="planner/default",
            kind="MessageKind.RESPOND",
            stage="DeliveryStage.SEND",
        )
        out = self._call(event)
        assert "autogen message" in out
        assert "kind=MessageKind.RESPOND" in out
        assert "stage=DeliveryStage.SEND" in out
        assert "sender=planner/default" in out

    def test_receiver_none_shown(self):
        event = _make_message_event(receiver=None)
        out = self._call(event)
        assert "receiver=None" in out

    def test_receiver_string_shown(self):
        event = _make_message_event(receiver="executor/default")
        out = self._call(event)
        assert "receiver=executor/default" in out

    def test_payload_type_and_source_extracted(self):
        inner = _make_text_message_payload(source="planner", msg_id="id42")
        event = _make_message_event(payload_obj=inner)
        out = self._call(event)
        assert "payload_type=TextMessage" in out
        assert "source=planner" in out
        assert "id=id42" in out

    def test_content_truncated_to_220_chars(self):
        long_content = "x" * 400
        inner = _make_text_message_payload(content=long_content)
        event = _make_message_event(payload_obj=inner)
        out = self._call(event, max_chars=2000)
        # content field must appear, but be capped at ~220
        assert "content=" in out
        content_part = out.split("content=", 1)[1]
        assert len(content_part) <= 240  # 220 + truncation marker overhead

    def test_usage_tokens_shown(self):
        inner = {**_make_text_message_payload(), "models_usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        event = _make_message_event(payload_obj=inner)
        out = self._call(event)
        assert "tokens=10/20" in out

    def test_no_payload_no_crash(self):
        event = _make_message_event(payload_obj=None)
        out = self._call(event)
        assert "autogen message" in out

    def test_invalid_payload_string_no_crash(self):
        event = {**_make_message_event(), "payload": "not-json{{{"}
        out = self._call(event)
        assert "autogen message" in out

    def test_max_chars_applied(self):
        inner = _make_text_message_payload(content="a" * 300)
        event = _make_message_event(payload_obj=inner, sender="agent/x", kind="K", stage="S")
        out = self._call(event, max_chars=80)
        assert len(out) <= 80 + 20  # some marker overhead


# ---------------------------------------------------------------------------
# Tests for _summarize_autogen_event_message (the outer dispatcher)
# dispatching to message-event branch
# ---------------------------------------------------------------------------


class TestSummarizeAutogenEventMessageDispatch:
    """_summarize_autogen_event_message dispatches Message events to the inner helper."""

    def _call(self, payload_dict: dict, max_chars: int = 500, max_tools: int = 3) -> str:
        from fateforger.core.logging_config import _summarize_autogen_event_message
        return _summarize_autogen_event_message(
            json.dumps(payload_dict), max_chars=max_chars, max_tools=max_tools
        )

    def test_message_event_dispatched(self):
        inner = _make_text_message_payload(content="Hello")
        event = _make_message_event(payload_obj=inner)
        out = self._call(event)
        assert "autogen message" in out
        assert "kind=MessageKind.RESPOND" in out

    def test_short_message_returned_as_is(self):
        msg = "short plain log line"
        from fateforger.core.logging_config import _summarize_autogen_event_message
        out = _summarize_autogen_event_message(msg, max_chars=500, max_tools=3)
        assert out == msg

    def test_non_json_truncated(self):
        from fateforger.core.logging_config import _summarize_autogen_event_message
        long_text = "abc" * 300
        out = _summarize_autogen_event_message(long_text, max_chars=100, max_tools=3)
        assert len(out) <= 120


# ---------------------------------------------------------------------------
# Tests for the LLM event path of _summarize_autogen_event_message
# ---------------------------------------------------------------------------


def _make_llm_event(
    *,
    agent_id: str = "planner",
    model: str = "gpt-4o",
    finish_reason: str | None = "stop",
    tool_call_names: list[str] | None = None,
    prompt_tokens: int | None = 100,
    completion_tokens: int | None = 25,
    total_tokens: int | None = 125,
    error: str | None = None,
) -> dict:
    """Build a minimal LLMCall event dict as emitted by AutoGen."""
    choices = []
    if finish_reason is not None:
        msg = {}
        if tool_call_names:
            msg["tool_calls"] = [
                {"function": {"name": n}} for n in tool_call_names
            ]
        choices.append({"finish_reason": finish_reason, "message": msg})
    usage: dict = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        usage["completion_tokens"] = completion_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    return {
        "type": "LLMCall",
        "agent_id": agent_id,
        "model": model,
        "response": {
            "model": model,
            "choices": choices,
            "usage": usage,
            **({"error": error} if error else {}),
        },
    }


class TestSummarizeAutogenEventMessageLLMPath:
    """_summarize_autogen_event_message summary for LLMEventPayload events."""

    def _call(self, payload: dict, max_chars: int = 500, max_tools: int = 5) -> str:
        import json
        from fateforger.core.logging_config import _summarize_autogen_event_message
        return _summarize_autogen_event_message(json.dumps(payload), max_chars=max_chars, max_tools=max_tools)

    def test_type_and_agent_in_output(self) -> None:
        out = self._call(_make_llm_event())
        assert "LLMCall" in out
        assert "planner" in out

    def test_model_name_in_output(self) -> None:
        out = self._call(_make_llm_event(model="gpt-4o-mini"))
        assert "gpt-4o-mini" in out

    def test_finish_reason_in_output(self) -> None:
        out = self._call(_make_llm_event(finish_reason="stop"))
        assert "finish=stop" in out

    def test_tool_calls_in_output(self) -> None:
        out = self._call(_make_llm_event(finish_reason="tool_calls", tool_call_names=["list-events", "get-event"]))
        assert "list-events" in out
        assert "get-event" in out

    def test_tokens_in_output(self) -> None:
        out = self._call(_make_llm_event(prompt_tokens=100, completion_tokens=25, total_tokens=125))
        assert "100" in out and "25" in out

    def test_no_finish_reason_omitted(self) -> None:
        out = self._call(_make_llm_event(finish_reason=None))
        assert "finish=" not in out

    def test_empty_usage_omits_tokens(self) -> None:
        ev = _make_llm_event(prompt_tokens=None, completion_tokens=None, total_tokens=None)
        out = self._call(ev)
        assert "tokens=" not in out

    def test_respects_max_chars(self) -> None:
        out = self._call(_make_llm_event(), max_chars=30)
        assert len(out) <= 40  # some tolerance

    def test_tool_list_in_output(self) -> None:
        ev = _make_llm_event()
        ev["tools"] = [{"function": {"name": t}} for t in ["alpha", "beta", "gamma"]]
        out = self._call(ev, max_tools=3)
        assert "3" in out or "alpha" in out
