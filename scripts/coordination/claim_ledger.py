"""The local, per-process record of what this session has claimed.

Why a local ledger exists at all: the ``Stop`` hook fires at the end of **every**
assistant turn, and there are ~49 sessions sharing one GitHub rate-limit bucket.
A hook that asked GitHub "what do I hold?" on every turn would spend the whole
fleet's REST budget on a question it can answer from disk. With an empty ledger
the hook makes **zero** network calls, which is the common case.

The ledger is a cache, never the authority. The authority is the ref. Anything
the ledger gets wrong is corrected by the sweeper, which reads refs.

Keyed by pid, not by session id: 48 live claude processes on this machine carry
only 25 distinct session ids, because resuming a conversation starts a second
process under the same id.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

LEDGER_DIR = pathlib.Path(
    os.environ.get("FATEFORGER_CLAIM_DIR", str(pathlib.Path.home() / ".fateforger" / "claims"))
)


def ledger_path(pid: int) -> pathlib.Path:
    return LEDGER_DIR / f"{pid}.json"


def load(pid: int) -> dict[str, Any]:
    path = ledger_path(pid)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"pid": pid, "claims": {}}
    data.setdefault("pid", pid)
    data.setdefault("claims", {})
    return data


def save(pid: int, data: dict[str, Any]) -> None:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = ledger_path(pid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def record_claim(pid: int, key: str, entry: dict[str, Any]) -> None:
    data = load(pid)
    data["claims"][key] = entry
    save(pid, data)


def forget_claim(pid: int, key: str) -> None:
    data = load(pid)
    data["claims"].pop(key, None)
    save(pid, data)


def stamp_idle(pid: int, when: str, session_id: str | None, name: str | None) -> None:
    """Record that this process finished a turn.

    This is the one thing the ``Stop`` hook uniquely knows and nothing else can
    observe for free: a turn boundary. ``(pid, procStart)`` proves a process is
    *running*; it cannot tell a session working from a session abandoned in a
    tab. The gap between now and the last stamp can.
    """
    data = load(pid)
    data["last_stop_at"] = when
    if session_id:
        data["session_id"] = session_id
    if name:
        data["session_name"] = name
    save(pid, data)


def all_ledgers() -> dict[int, dict[str, Any]]:
    if not LEDGER_DIR.is_dir():
        return {}
    out: dict[int, dict[str, Any]] = {}
    for path in LEDGER_DIR.glob("*.json"):
        try:
            out[int(path.stem)] = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return out
