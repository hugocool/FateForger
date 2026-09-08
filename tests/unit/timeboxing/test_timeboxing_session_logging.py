"""Unit tests for per-session timeboxing debug log files."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent


def test_session_debug_logging_writes_session_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session debug events should be written to a dedicated session log file."""
    monkeypatch.setenv("TIMEBOX_SESSION_DEBUG_LOG", "1")
    monkeypatch.setenv("TIMEBOX_SESSION_LOG_DIR", str(tmp_path))
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._session_debug_loggers = {}
    session = Session(
        thread_ts="1771026031.540599",
        channel_id="C0AA6HC1RJL",
        user_id="U095637NL8P",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
        session_key="C0AA6HC1RJL:1771026031.540599",
    )

    TimeboxingFlowAgent._session_debug(
        agent,
        session,
        "calendar_prefetch_start",
        timeout_s=4.0,
    )
    TimeboxingFlowAgent._close_session_debug_logger(agent, session.session_key or "")

    assert session.debug_log_path is not None
    log_path = Path(session.debug_log_path)
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert '"event": "calendar_prefetch_start"' in content
    assert '"session_key": "C0AA6HC1RJL:1771026031.540599"' in content


def test_session_debug_logging_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session debug logs should stay off when explicit env flag is false."""
    monkeypatch.setenv("TIMEBOX_SESSION_DEBUG_LOG", "0")
    monkeypatch.setenv("TIMEBOX_SESSION_LOG_DIR", str(tmp_path))
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._session_debug_loggers = {}
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="UTC",
        session_key="c1:t1",
    )

    TimeboxingFlowAgent._session_debug(agent, session, "noop")

    assert session.debug_log_path is None
    assert list(tmp_path.iterdir()) == []
