"""Tests for StructuredJsonFormatter.

Verifies that the formatter produces a valid, normalized JSON envelope
suitable for Loki ingestion via Promtail / Docker log scraping.
"""

from __future__ import annotations

import json
import logging

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    msg: str,
    *,
    name: str = "test.logger",
    level: int = logging.INFO,
    exc_info: bool = False,
    **extra: object,
) -> logging.LogRecord:
    """Build a minimal LogRecord without going through a real logger."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if exc_info:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record.exc_info = sys.exc_info()
    for k, v in extra.items():
        setattr(record, k, v)
    return record


# ---------------------------------------------------------------------------
# Baseline envelope tests
# ---------------------------------------------------------------------------


class TestStructuredJsonFormatterEnvelope:
    """StructuredJsonFormatter must always produce a valid JSON envelope."""

    @pytest.fixture
    def formatter(self):
        from fateforger.core.logging_config import StructuredJsonFormatter

        return StructuredJsonFormatter()

    def test_plain_text_produces_valid_json(self, formatter):
        record = _make_record("hello world")
        out = formatter.format(record)
        parsed = json.loads(out)
        assert parsed["message"] == "hello world"

    def test_required_keys_always_present(self, formatter):
        record = _make_record("hi")
        parsed = json.loads(formatter.format(record))
        assert {"ts", "level", "logger", "message"} <= parsed.keys()

    def test_level_is_lowercase(self, formatter):
        record = _make_record("warn", level=logging.WARNING)
        parsed = json.loads(formatter.format(record))
        assert parsed["level"] == "warning"

    def test_ts_is_rfc3339z(self, formatter):
        record = _make_record("ts test")
        parsed = json.loads(formatter.format(record))
        ts = parsed["ts"]
        assert ts.endswith("Z"), f"ts should end with Z, got {ts!r}"

    def test_logger_name_preserved(self, formatter):
        record = _make_record("x", name="fateforger.agents.timeboxing.agent")
        parsed = json.loads(formatter.format(record))
        assert parsed["logger"] == "fateforger.agents.timeboxing.agent"

    def test_exception_info_included(self, formatter):
        record = _make_record("oops", exc_info=True)
        parsed = json.loads(formatter.format(record))
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]

    def test_always_valid_json_on_bad_message(self, formatter):
        record = _make_record("{broken json }")
        out = formatter.format(record)
        parsed = json.loads(out)
        assert "message" in parsed


# ---------------------------------------------------------------------------
# AutoGen event JSON message extraction
# ---------------------------------------------------------------------------


class TestStructuredJsonFormatterEventExtraction:
    """When record.msg is an AutoGen event JSON, extract structured fields."""

    @pytest.fixture
    def formatter(self):
        from fateforger.core.logging_config import StructuredJsonFormatter

        return StructuredJsonFormatter()

    def _make_autogen_record(self, payload: dict) -> logging.LogRecord:
        return _make_record(json.dumps(payload), name="autogen_core.events")

    def test_extracts_event_type(self, formatter):
        record = self._make_autogen_record({"type": "LLMCall", "agent_id": "planner"})
        parsed = json.loads(formatter.format(record))
        assert parsed.get("event") == "LLMCall"

    def test_extracts_agent_id(self, formatter):
        record = self._make_autogen_record({"type": "LLMCall", "agent_id": "my_agent"})
        parsed = json.loads(formatter.format(record))
        assert parsed.get("agent") == "my_agent"

    def test_extracts_stage(self, formatter):
        record = self._make_autogen_record({"type": "LLMCall", "stage": "refine"})
        parsed = json.loads(formatter.format(record))
        assert parsed.get("stage") == "refine"

    def test_extracts_model(self, formatter):
        record = self._make_autogen_record({"type": "LLMCall", "model": "gpt-4o"})
        parsed = json.loads(formatter.format(record))
        assert parsed.get("model") == "gpt-4o"

    def test_extracts_session_key(self, formatter):
        record = self._make_autogen_record(
            {"type": "LLMCall", "session_key": "sess-123"}
        )
        parsed = json.loads(formatter.format(record))
        assert parsed.get("session_key") == "sess-123"

    def test_extracts_thread_ts(self, formatter):
        record = self._make_autogen_record(
            {"type": "LLMCall", "thread_ts": "1234567.890"}
        )
        parsed = json.loads(formatter.format(record))
        assert parsed.get("thread_ts") == "1234567.890"

    def test_extracts_tool_name(self, formatter):
        record = self._make_autogen_record(
            {"type": "ToolCall", "tool_name": "list-events"}
        )
        parsed = json.loads(formatter.format(record))
        assert parsed.get("tool_name") == "list-events"

    def test_missing_optional_fields_absent(self, formatter):
        record = self._make_autogen_record({"type": "LLMCall"})
        parsed = json.loads(formatter.format(record))
        assert "stage" not in parsed
        assert "model" not in parsed
        assert "agent" not in parsed

    def test_non_event_json_uses_plain_message(self, formatter):
        """A JSON object that isn't an AutoGen event is treated as plain text."""
        payload = {"foo": "bar", "baz": 42}
        record = _make_record(json.dumps(payload), name="some.other.logger")
        parsed = json.loads(formatter.format(record))
        assert "message" in parsed

    def test_message_field_is_string(self, formatter):
        record = self._make_autogen_record({"type": "LLMCall", "agent_id": "x"})
        parsed = json.loads(formatter.format(record))
        assert isinstance(parsed["message"], str)


# ---------------------------------------------------------------------------
# Redaction of sensitive fields
# ---------------------------------------------------------------------------


class TestStructuredJsonFormatterRedaction:
    """Sensitive fields in the JSON payload must be redacted."""

    @pytest.fixture
    def formatter(self):
        from fateforger.core.logging_config import StructuredJsonFormatter

        return StructuredJsonFormatter()

    def test_api_key_redacted(self, formatter):
        payload = json.dumps({"type": "LLMCall", "api_key": "sk-secret"})
        record = _make_record(payload, name="autogen_core.events")
        out = formatter.format(record)
        assert "sk-secret" not in out

    def test_authorization_redacted(self, formatter):
        payload = json.dumps({"type": "LLMCall", "authorization": "Bearer token123"})
        record = _make_record(payload, name="autogen_core.events")
        out = formatter.format(record)
        assert "token123" not in out

    def test_non_sensitive_fields_preserved(self, formatter):
        payload = json.dumps({"type": "LLMCall", "model": "gpt-4o", "stage": "plan"})
        record = _make_record(payload, name="autogen_core.events")
        parsed = json.loads(formatter.format(record))
        assert parsed.get("model") == "gpt-4o"
        assert parsed.get("stage") == "plan"


# ---------------------------------------------------------------------------
# configure_logging integration: OBS_LOG_FORMAT=json
# ---------------------------------------------------------------------------


class TestConfigureLoggingJsonMode:
    """When OBS_LOG_FORMAT=json, the root stdout handler uses StructuredJsonFormatter."""

    @pytest.fixture(autouse=True)
    def reset_root_handlers(self):
        """Isolate each test from global logging state set by configure_logging()."""
        root = logging.getLogger()
        saved_handlers = root.handlers[:]
        saved_formatters = {h: h.formatter for h in root.handlers}
        saved_level = root.level
        yield
        root.handlers[:] = saved_handlers
        root.level = saved_level
        for h, fmt in saved_formatters.items():
            h.formatter = fmt

    def test_json_formatter_installed_when_env_set(self, monkeypatch):
        from fateforger.core.logging_config import (
            StructuredJsonFormatter,
            configure_logging,
        )

        monkeypatch.setenv("OBS_LOG_FORMAT", "json")
        monkeypatch.setenv("OBS_PROMETHEUS_ENABLED", "0")  # avoid port side effects
        configure_logging(default_level="WARNING")
        root = logging.getLogger()
        stream_formatters = [
            type(h.formatter) for h in root.handlers if hasattr(h, "stream")
        ]
        assert (
            StructuredJsonFormatter in stream_formatters
        ), f"Expected StructuredJsonFormatter in root stream handlers, got {stream_formatters}"

    def test_json_formatter_not_installed_by_default(self, monkeypatch):
        from fateforger.core.logging_config import (
            StructuredJsonFormatter,
            configure_logging,
        )

        monkeypatch.delenv("OBS_LOG_FORMAT", raising=False)
        monkeypatch.setenv("OBS_PROMETHEUS_ENABLED", "0")
        configure_logging(default_level="WARNING")
        root = logging.getLogger()
        stream_formatters = [
            type(h.formatter) for h in root.handlers if hasattr(h, "stream")
        ]
        assert StructuredJsonFormatter not in stream_formatters
