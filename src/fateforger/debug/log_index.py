"""Utilities for writing and reading JSONL log index files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def append_index_entry(*, index_path: Path, entry: dict[str, Any]) -> None:
    """Append one JSONL entry to the given index path."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str))
        handle.write("\n")


def read_index_entries(*, index_path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read JSONL index entries, newest-first when ``limit`` is provided."""
    if not index_path.exists():
        return []
    with index_path.open("r", encoding="utf-8") as handle:
        rows = [_parse_row(line) for line in handle]
    entries = [row for row in rows if row is not None]
    if limit is None:
        return entries
    capped = max(0, int(limit))
    if capped == 0:
        return []
    return entries[-capped:]


def _parse_row(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def newest_existing_entries(
    *, entries: Iterable[dict[str, Any]], path_key: str = "log_path"
) -> list[dict[str, Any]]:
    """Filter index entries to ones whose log file still exists."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        raw_path = str(entry.get(path_key, "")).strip()
        if not raw_path:
            continue
        if Path(raw_path).exists():
            out.append(entry)
    return out
