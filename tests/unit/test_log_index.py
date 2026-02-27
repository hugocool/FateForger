"""Unit tests for JSONL log index helpers."""

from __future__ import annotations

from pathlib import Path

from fateforger.debug.log_index import (
    append_index_entry,
    newest_existing_entries,
    read_index_entries,
)


def test_append_and_read_index_entries(tmp_path: Path) -> None:
    """Appending entries should produce readable JSONL rows."""
    index_path = tmp_path / "index.jsonl"
    append_index_entry(
        index_path=index_path,
        entry={"type": "timeboxing_session", "session_key": "a", "log_path": "x.log"},
    )
    append_index_entry(
        index_path=index_path,
        entry={"type": "timeboxing_session", "session_key": "b", "log_path": "y.log"},
    )

    rows = read_index_entries(index_path=index_path)
    assert len(rows) == 2
    assert rows[0]["session_key"] == "a"
    assert rows[1]["session_key"] == "b"


def test_read_index_entries_limit_returns_newest_rows(tmp_path: Path) -> None:
    """Limit should keep insertion order and return only the newest rows."""
    index_path = tmp_path / "index.jsonl"
    for idx in range(5):
        append_index_entry(
            index_path=index_path,
            entry={"type": "session", "session_key": f"s{idx}", "log_path": f"{idx}.log"},
        )

    rows = read_index_entries(index_path=index_path, limit=2)
    assert [row["session_key"] for row in rows] == ["s3", "s4"]


def test_newest_existing_entries_filters_missing_logs(tmp_path: Path) -> None:
    """Only entries pointing to existing files should be returned."""
    existing = tmp_path / "exists.log"
    existing.write_text("ok\n", encoding="utf-8")
    entries = [
        {"log_path": str(tmp_path / "missing.log"), "session_key": "missing"},
        {"log_path": str(existing), "session_key": "exists"},
    ]

    rows = newest_existing_entries(entries=entries)
    assert len(rows) == 1
    assert rows[0]["session_key"] == "exists"


def test_llm_index_entry_roundtrip(tmp_path: Path) -> None:
    """LLM audit index rows should roundtrip like other JSONL entries."""
    index_path = tmp_path / "llm_io_index.jsonl"
    append_index_entry(
        index_path=index_path,
        entry={"type": "llm_io", "thread_ts": "123.45", "log_path": "llm.log"},
    )

    rows = read_index_entries(index_path=index_path)
    assert len(rows) == 1
    assert rows[0]["type"] == "llm_io"
    assert rows[0]["thread_ts"] == "123.45"
