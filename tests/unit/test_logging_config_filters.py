"""Unit tests for logging filter resilience in ``logging_config``."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fateforger.core.logging_config import (
    _AutogenCoreFilter,
    _AutogenEventsFilter,
    _SafeRecordMessageFilter,
    _sanitize_for_audit,
    configure_logging,
)


class _BadStrMessage:
    """Mimic a message object that fails during ``str()`` conversion."""

    def __init__(self) -> None:
        self.kwargs = {"planned_date": date(2026, 2, 14), "kind": "example"}

    def __str__(self) -> str:
        raise TypeError("Object of type date is not JSON serializable")


def _record(*, logger_name: str, msg: object) -> logging.LogRecord:
    """Build a synthetic log record for filter tests."""
    return logging.LogRecord(
        name=logger_name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_autogen_events_filter_handles_unserializable_message() -> None:
    """Events filter should summarize even when ``record.getMessage`` would fail."""
    filt = _AutogenEventsFilter(mode="summary", max_chars=900, max_tools=10)
    record = _record(logger_name="autogen_core.events", msg=_BadStrMessage())

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "planned_date" in record.msg


def test_autogen_events_filter_full_mode_handles_unserializable_message() -> None:
    """Events filter should also coerce safely when full logging mode is enabled."""
    filt = _AutogenEventsFilter(mode="full", max_chars=900, max_tools=10)
    record = _record(logger_name="autogen_core.events", msg=_BadStrMessage())

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "planned_date" in record.msg


def test_autogen_events_filter_summary_mode_sanitizes_dict_payload() -> None:
    """Summary mode should not leak raw payload content from dict messages."""
    filt = _AutogenEventsFilter(mode="summary", max_chars=900, max_tools=10)
    record = _record(
        logger_name="autogen_core.events",
        msg={
            "type": "LLMCall",
            "agent_id": "tasks_agent",
            "messages": [{"role": "user", "content": "my secret token"}],
            "response": {
                "model": "openrouter/google/gemini-2.5-pro",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        },
    )

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "autogen type=LLMCall" in record.msg
    assert "my secret token" not in record.msg


def test_autogen_events_filter_full_mode_sanitized_default() -> None:
    """Full mode defaults to sanitized payload output."""
    filt = _AutogenEventsFilter(mode="full", max_chars=900, max_tools=10)
    record = _record(
        logger_name="autogen_core.events",
        msg={"type": "Message", "api_key": "abc123", "channel_id": "C123"},
    )

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "***REDACTED***" in record.msg
    assert "abc123" not in record.msg


def test_autogen_events_filter_audit_target_suppresses_stdout() -> None:
    """Audit target should suppress stdout while keeping observability side effects."""
    filt = _AutogenEventsFilter(
        mode="summary", max_chars=900, max_tools=10, output_target="audit"
    )
    record = _record(
        logger_name="autogen_core.events",
        msg={"type": "Message", "payload": '{"type":"TextMessage","content":"hello"}'},
    )

    allowed = filt.filter(record)
    assert allowed is False


def test_autogen_core_filter_handles_unserializable_message() -> None:
    """Core filter should pass safely when ``record.getMessage`` would fail."""
    filt = _AutogenCoreFilter(max_chars=900)
    record = _record(logger_name="autogen_core", msg=_BadStrMessage())

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "coerced-log-payload" in record.msg


def test_safe_record_filter_handles_unserializable_message() -> None:
    """Generic safe filter should coerce bad message objects into strings."""
    filt = _SafeRecordMessageFilter()
    record = _record(
        logger_name="autogen_ext.models.openai._openai_client", msg=_BadStrMessage()
    )

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "coerced-log-payload" in record.msg


def test_configure_logging_adds_timebox_patcher_file_handler(
    tmp_path: Path, monkeypatch
) -> None:
    """Debug logger setup should attach a timestamped file handler for patching logs."""
    patcher_logger = logging.getLogger("fateforger.agents.timeboxing.patching")
    existing_handlers = list(patcher_logger.handlers)
    original_propagate = patcher_logger.propagate
    original_level = patcher_logger.level
    for handler in existing_handlers:
        patcher_logger.removeHandler(handler)

    monkeypatch.setenv("TIMEBOX_PATCHER_DEBUG_LOG", "1")
    monkeypatch.setenv("TIMEBOX_PATCHER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("TIMEBOX_PATCHER_INDEX_FILE", "patcher.index.jsonl")
    monkeypatch.setenv("OBS_PROMETHEUS_ENABLED", "0")
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")

    try:
        configure_logging(default_level="INFO")
        new_handlers = [
            h
            for h in patcher_logger.handlers
            if getattr(h, "_fftb_patcher_file", False)
        ]
        assert len(new_handlers) == 1
        file_handler = new_handlers[0]
        log_path = Path(getattr(file_handler, "baseFilename"))
        assert log_path.parent == tmp_path
        assert log_path.name.startswith("timebox_patcher_")
        assert log_path.suffix == ".log"
        index_path = tmp_path / "patcher.index.jsonl"
        assert index_path.exists()
        lines = index_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        assert "timebox_patcher" in lines[-1]
    finally:
        for handler in list(patcher_logger.handlers):
            patcher_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in existing_handlers:
            patcher_logger.addHandler(handler)
        patcher_logger.propagate = original_propagate
        patcher_logger.setLevel(original_level)


def test_configure_logging_adds_openai_safe_filter(monkeypatch) -> None:
    """configure_logging should protect OpenAI client logs from serialization errors."""
    openai_logger = logging.getLogger("autogen_ext.models.openai._openai_client")
    existing_filters = list(openai_logger.filters)
    for existing in existing_filters:
        openai_logger.removeFilter(existing)
    monkeypatch.delenv("TIMEBOX_PATCHER_DEBUG_LOG", raising=False)
    monkeypatch.setenv("OBS_PROMETHEUS_ENABLED", "0")
    monkeypatch.setenv("OBS_LLM_AUDIT_ENABLED", "0")

    try:
        configure_logging(default_level="INFO")
        safe_filters = [
            filt for filt in openai_logger.filters if isinstance(filt, _SafeRecordMessageFilter)
        ]
        assert len(safe_filters) == 1
    finally:
        for filt in list(openai_logger.filters):
            openai_logger.removeFilter(filt)
        for filt in existing_filters:
            openai_logger.addFilter(filt)


def test_sanitize_for_audit_redacts_and_truncates() -> None:
    """Sanitized audit payloads must redact sensitive keys and cap long strings."""
    payload = {
        "session_key": "s1",
        "api_key": "abc123",
        "nested": {"authorization_header": "Bearer SECRET"},
        "messages": [{"content": "x" * 120}],
        "prompt_tokens": 123,
    }

    sanitized = _sanitize_for_audit(payload, max_chars=40)

    assert sanitized["api_key"] == "***REDACTED***"
    assert sanitized["nested"]["authorization_header"] == "***REDACTED***"
    assert sanitized["messages"][0]["content"].startswith("x")
    assert "truncated" in sanitized["messages"][0]["content"]
    assert sanitized["prompt_tokens"] == 123
