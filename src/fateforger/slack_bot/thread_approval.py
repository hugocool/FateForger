"""Where a Slack Approve press meets the harness commit gate.

The two halves never share a process: the gate is a hook running inside the
harness subprocess, and the button is a Slack action arriving on the bot's
event loop, possibly minutes later. A file is what they can both reach.

Per thread rather than per turn, and that is forced rather than chosen. The
harness runs a turn to completion, so the plan is only shown *after* the commit
was refused -- Hugo presses Approve between turns, and a per-turn path would be
gone by the time he pressed it.

The path is derived from the thread, never minted inside `ask()`: a path the
harness invented would be invisible to the handler that has to write into it,
so the two would never meet and the gate would deny every commit while looking
correctly configured. That presents as the gate working, which is worse than
denying loudly.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def approval_path(thread_key: str) -> Path:
    """The file this conversation's approvals are written to.

    Named from a digest of the thread key rather than the key itself: a Slack
    channel id and timestamp are fine in a filename, but deriving the name
    keeps anything a user could influence out of a filesystem path, and the
    key is a system-minted identifier so hashing it loses nothing.
    """
    digest = hashlib.sha256(thread_key.encode("utf-8")).hexdigest()[:16]
    directory = Path(tempfile.gettempdir()) / "fateforger-approvals"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.approval"


def grant(thread_key: str, user_id: str) -> Path:
    """Record that a human approved the next commit in this thread.

    The token names who and when. Nothing reads it beyond "not empty" today,
    but a gate that consumes an anonymous token can never answer who approved
    a plan afterwards -- and that question is exactly what the journal's
    ACCEPTED disposition could not answer either.
    """
    path = approval_path(thread_key)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(f"approved by {user_id} at {stamp}\n", encoding="utf-8")
    return path


def revoke(thread_key: str) -> None:
    """Clear any unspent approval for this thread.

    Called when a plan changes materially, so an approval granted for the day
    Hugo saw cannot be spent on a different one.
    """
    try:
        approval_path(thread_key).write_text("", encoding="utf-8")
    except OSError:
        return


__all__ = ["approval_path", "grant", "revoke"]
