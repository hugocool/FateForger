#!/usr/bin/env python3
"""Fast query helper for timeboxing session/patcher logs."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from fateforger.debug.log_index import newest_existing_entries, read_index_entries


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query indexed timeboxing logs without ad-hoc grep."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sessions = sub.add_parser("sessions", help="List indexed session log files.")
    sessions.add_argument("--limit", type=int, default=10)
    sessions.add_argument("--session-key", default=None)
    sessions.add_argument("--thread-ts", default=None)
    sessions.add_argument("--channel-id", default=None)

    events = sub.add_parser("events", help="Query JSON events from a session log.")
    events.add_argument("--limit", type=int, default=100)
    events.add_argument("--session-key", default=None)
    events.add_argument("--thread-ts", default=None)
    events.add_argument("--channel-id", default=None)
    events.add_argument("--log-path", default=None)
    events.add_argument("--event", default=None)
    events.add_argument("--contains", default=None)

    patcher = sub.add_parser("patcher", help="List indexed patcher log files.")
    patcher.add_argument("--limit", type=int, default=10)

    llm = sub.add_parser("llm", help="Query indexed LLM I/O audit logs.")
    llm.add_argument("--limit", type=int, default=100)
    llm.add_argument("--session-key", default=None)
    llm.add_argument("--thread-ts", default=None)
    llm.add_argument("--call-label", default=None)
    llm.add_argument("--model", default=None)
    llm.add_argument("--status", default=None)
    llm.add_argument("--contains", default=None)
    llm.add_argument("--log-path", default=None)

    return parser


def _logs_dir() -> Path:
    return Path(os.getenv("TIMEBOX_SESSION_LOG_DIR", "logs"))


def _session_index_path() -> Path:
    return _logs_dir() / os.getenv(
        "TIMEBOX_SESSION_INDEX_FILE", "timeboxing_session_index.jsonl"
    )


def _patcher_index_path() -> Path:
    log_dir = Path(os.getenv("TIMEBOX_PATCHER_LOG_DIR", "logs"))
    return log_dir / os.getenv("TIMEBOX_PATCHER_INDEX_FILE", "timebox_patcher_index.jsonl")


def _llm_index_path() -> Path:
    log_dir = Path(os.getenv("OBS_LLM_AUDIT_LOG_DIR", "logs"))
    return log_dir / os.getenv("OBS_LLM_AUDIT_INDEX_FILE", "llm_io_index.jsonl")


def _filter_entries(
    entries: Iterable[dict[str, Any]],
    *,
    session_key: str | None,
    thread_ts: str | None,
    channel_id: str | None,
) -> list[dict[str, Any]]:
    filtered = list(entries)
    if session_key:
        filtered = [entry for entry in filtered if entry.get("session_key") == session_key]
    if thread_ts:
        filtered = [entry for entry in filtered if entry.get("thread_ts") == thread_ts]
    if channel_id:
        filtered = [entry for entry in filtered if entry.get("channel_id") == channel_id]
    return filtered


_SESSION_FILE_RE = re.compile(
    r"^timeboxing_session_(?P<ts>\d{8}_\d{6})_(?P<session>.+)_(?P<pid>\d+)\.log$"
)
_PATCHER_FILE_RE = re.compile(
    r"^timebox_patcher_(?P<ts>\d{8}_\d{6})_(?P<pid>\d+)\.log$"
)
_LLM_FILE_RE = re.compile(r"^llm_io_(?P<ts>\d{8}_\d{6})_(?P<pid>\d+)\.jsonl$")


def _discover_session_entries(log_dir: Path) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("timeboxing_session_*.log")):
        match = _SESSION_FILE_RE.match(path.name)
        if not match:
            continue
        discovered.append(
            {
                "type": "timeboxing_session",
                "created_at": match.group("ts"),
                "session_key": match.group("session"),
                "thread_ts": None,
                "channel_id": None,
                "log_path": str(path),
                "pid": int(match.group("pid")),
            }
        )
    return discovered


def _discover_patcher_entries(log_dir: Path) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("timebox_patcher_*.log")):
        match = _PATCHER_FILE_RE.match(path.name)
        if not match:
            continue
        discovered.append(
            {
                "type": "timebox_patcher",
                "created_at": match.group("ts"),
                "log_path": str(path),
                "pid": int(match.group("pid")),
            }
        )
    return discovered


def _discover_llm_entries(log_dir: Path) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("llm_io_*.jsonl")):
        match = _LLM_FILE_RE.match(path.name)
        if not match:
            continue
        discovered.append(
            {
                "type": "llm_io",
                "created_at": match.group("ts"),
                "log_path": str(path),
                "pid": int(match.group("pid")),
            }
        )
    return discovered


def _merge_entries(
    *, indexed: list[dict[str, Any]], discovered: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    indexed_paths = {str(entry.get("log_path", "")) for entry in indexed}
    extras = [
        entry
        for entry in discovered
        if str(entry.get("log_path", "")) not in indexed_paths
    ]
    return [*indexed, *extras]


def _entry_sort_key(entry: dict[str, Any]) -> tuple[float, str]:
    created_at = str(entry.get("created_at") or "").strip()
    timestamp = 0.0
    if created_at:
        if re.match(r"^\d{8}_\d{6}$", created_at):
            try:
                timestamp = datetime.strptime(created_at, "%Y%m%d_%H%M%S").timestamp()
            except ValueError:
                timestamp = 0.0
        else:
            iso_value = created_at.replace("Z", "+00:00")
            try:
                timestamp = datetime.fromisoformat(iso_value).timestamp()
            except ValueError:
                timestamp = 0.0
    if timestamp <= 0:
        path = Path(str(entry.get("log_path") or ""))
        if path.exists():
            timestamp = path.stat().st_mtime
    return timestamp, str(entry.get("log_path") or "")


def _print_json_lines(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


def _parse_session_event_line(line: str) -> dict[str, Any] | None:
    marker = line.find("{")
    if marker < 0:
        return None
    raw = line[marker:].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _read_session_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        events = [_parse_session_event_line(line) for line in handle]
    return [event for event in events if event is not None]


def _run_sessions(args: argparse.Namespace) -> int:
    session_log_dir = _logs_dir()
    indexed = newest_existing_entries(
        entries=read_index_entries(index_path=_session_index_path())
    )
    merged = _merge_entries(
        indexed=indexed, discovered=_discover_session_entries(session_log_dir)
    )
    filtered = _filter_entries(
        merged,
        session_key=args.session_key,
        thread_ts=args.thread_ts,
        channel_id=args.channel_id,
    )
    filtered = sorted(filtered, key=_entry_sort_key)
    limit = max(1, int(args.limit))
    _print_json_lines(filtered[-limit:])
    return 0


def _run_events(args: argparse.Namespace) -> int:
    selected_path = str(args.log_path or "").strip()
    if not selected_path:
        indexed = newest_existing_entries(
            entries=read_index_entries(index_path=_session_index_path())
        )
        merged = _merge_entries(
            indexed=indexed, discovered=_discover_session_entries(_logs_dir())
        )
        filtered = _filter_entries(
            merged,
            session_key=args.session_key,
            thread_ts=args.thread_ts,
            channel_id=args.channel_id,
        )
        if not filtered:
            return 1
        selected_path = str(filtered[-1]["log_path"])

    events = _read_session_events(Path(selected_path))
    event_name = str(args.event or "").strip()
    if event_name:
        events = [event for event in events if event.get("event") == event_name]
    contains = str(args.contains or "").strip().lower()
    if contains:
        events = [
            event
            for event in events
            if contains in json.dumps(event, ensure_ascii=False).lower()
        ]
    limit = max(1, int(args.limit))
    _print_json_lines(events[-limit:])
    return 0


def _run_patcher(args: argparse.Namespace) -> int:
    patcher_log_dir = Path(os.getenv("TIMEBOX_PATCHER_LOG_DIR", "logs"))
    indexed = newest_existing_entries(
        entries=read_index_entries(index_path=_patcher_index_path())
    )
    merged = _merge_entries(
        indexed=indexed, discovered=_discover_patcher_entries(patcher_log_dir)
    )
    merged = sorted(merged, key=_entry_sort_key)
    limit = max(1, int(args.limit))
    _print_json_lines(merged[-limit:])
    return 0


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _run_llm(args: argparse.Namespace) -> int:
    selected_path = str(args.log_path or "").strip()
    if selected_path:
        rows = _read_json_lines(Path(selected_path))
        for row in rows:
            row.setdefault("log_path", selected_path)
    else:
        llm_log_dir = Path(os.getenv("OBS_LLM_AUDIT_LOG_DIR", "logs"))
        indexed = newest_existing_entries(entries=read_index_entries(index_path=_llm_index_path()))
        merged = _merge_entries(indexed=indexed, discovered=_discover_llm_entries(llm_log_dir))
        if not merged:
            return 1
        merged = sorted(merged, key=_entry_sort_key)
        rows: list[dict[str, Any]] = []
        for entry in merged:
            path = str(entry.get("log_path") or "")
            if not path:
                continue
            file_rows = _read_json_lines(Path(path))
            for row in file_rows:
                row.setdefault("log_path", path)
            rows.extend(file_rows)

    session_key = str(args.session_key or "").strip()
    if session_key:
        rows = [row for row in rows if row.get("session_key") == session_key]
    thread_ts = str(args.thread_ts or "").strip()
    if thread_ts:
        rows = [row for row in rows if row.get("thread_ts") == thread_ts]
    call_label = str(args.call_label or "").strip()
    if call_label:
        rows = [row for row in rows if row.get("call_label") == call_label]
    model = str(args.model or "").strip()
    if model:
        rows = [row for row in rows if row.get("model") == model]
    status = str(args.status or "").strip()
    if status:
        rows = [row for row in rows if row.get("status") == status]
    contains = str(args.contains or "").strip().lower()
    if contains:
        rows = [row for row in rows if contains in json.dumps(row, ensure_ascii=False).lower()]

    limit = max(1, int(args.limit))
    _print_json_lines(rows[-limit:])
    return 0


def main() -> int:
    load_dotenv(".env")
    parser = _build_parser()
    args = parser.parse_args()
    match args.command:
        case "sessions":
            return _run_sessions(args)
        case "events":
            return _run_events(args)
        case "patcher":
            return _run_patcher(args)
        case "llm":
            return _run_llm(args)
        case _:
            parser.error(f"Unsupported command: {args.command}")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
