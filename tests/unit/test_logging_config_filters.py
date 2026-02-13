"""Unit tests for logging filter resilience in ``logging_config``."""

from __future__ import annotations

import logging
from datetime import date

from fateforger.core.logging_config import _AutogenCoreFilter, _AutogenEventsFilter


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


def test_autogen_core_filter_handles_unserializable_message() -> None:
    """Core filter should pass safely when ``record.getMessage`` would fail."""
    filt = _AutogenCoreFilter(max_chars=900)
    record = _record(logger_name="autogen_core", msg=_BadStrMessage())

    allowed = filt.filter(record)

    assert allowed is True
    assert isinstance(record.msg, str)
    assert "coerced-log-payload" in record.msg
