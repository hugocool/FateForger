"""Unit tests for logging filter resilience in ``logging_config``."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fateforger.core.logging_config import (
    _AutogenCoreFilter,
    _AutogenEventsFilter,
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
    assert "coerced-log-payload" in record.msg


def test_autogen_events_filter_full_mode_handles_unserializable_message() -> None:
    """Events filter should also coerce safely when full logging mode is enabled."""
    filt = _AutogenEventsFilter(mode="full", max_chars=900, max_tools=10)
    record = _record(logger_name="autogen_core.events", msg=_BadStrMessage())

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "coerced-log-payload" in record.msg


def test_autogen_core_filter_handles_unserializable_message() -> None:
    """Core filter should pass safely when ``record.getMessage`` would fail."""
    filt = _AutogenCoreFilter(max_chars=900)
    record = _record(logger_name="autogen_core", msg=_BadStrMessage())

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
