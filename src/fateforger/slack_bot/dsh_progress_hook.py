"""A DeepSeek Harness ``PostToolUse`` hook that reports one completed tool call.

The harness ships a Claude-Code-dialect hook bridge, so the cheapest way to see
inside a run is to let it tell us: this script is invoked once per completed
tool call with the event on stdin, and appends a single line naming the tool.
``harness_bridge`` tails that file and turns each line into a progress step.

**Why a file and not the harness's own output.** Two earlier attempts are worth
not repeating. Reading the MCP servers' stderr worked only while they ran under
stdio and went blind the moment they moved to ``streamable-http``. Tailing from
inside ``for line in proc.stdout`` could never have fired at all, because that
loop blocks and a warm run emits nothing until the answer. A file written by the
hook and polled from a separate thread depends on neither transport nor timing.

**Why this never fails the run.** A progress reporter that blocks the work it
reports on is worse than no progress at all, so every path here exits 0 and
writes nothing to stdout — under the hook protocol, exit 2 blocks the tool call
and stdout is parsed as a decision. Problems go to stderr, which the bridge
records as a ``hook/result`` summary: visible in the harness log, inert to the
agent loop. That is the one place silence is the right failure, and it is
bounded to a cosmetic channel.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Set per-run by ``harness_bridge``. Absent means nobody is listening — an
#: ordinary headless run rather than a Slack turn — so there is nothing to do.
PROGRESS_FILE_ENV = "FF_DSH_PROGRESS_FILE"

#: What each tool is called when a person reads it.
#:
#: These keys are harness- and server-minted identifiers -- they name tools
#: this system published, not anything a user wrote -- so selecting on them is
#: identification and not a judgement about meaning. An unknown tool is
#: reported under its own name rather than dropped: a step nobody labelled is
#: still a step that happened, and hiding it would make a slow run look idle.
_LABELS = {
    "mcp__tmbx__plan_read": "Reading the day",
    "mcp__tmbx__plan_apply": "Drafting the changes",
    "mcp__tmbx__plan_commit": "Writing it to the calendar",
    "mcp__tmbx__plan_undo": "Undoing the last change",
    "mcp__tmbx__plan_history": "Checking what changed",
    "mcp__memory__memory_get_active_constraints": "Loading your rules",
    "mcp__memory__memory_get_suspended_constraints": "Checking what is suspended today",
    "mcp__memory__memory_get_session_constraints": "Recalling this conversation",
    "mcp__memory__memory_observe": "Remembering what you said",
    "skill": "Getting my bearings",
    "todo_write": "Sketching the steps",
}

#: Written ahead of the tool call and again after it, so a step appears while
#: it is running instead of only once it is over. The gap this closes is the
#: whole complaint: the first tool result landed 5.6s in, and until then the
#: thread said nothing at all.
START = "start"
DONE = "done"


def label_for(tool: str) -> str:
    """The human name for a tool, or the tool's own name if it has none."""
    return _LABELS.get(tool, tool)


def step_line(event: dict) -> str | None:
    """Render one progress line, or ``None`` if the event names no tool.

    ``tool_name`` is a harness-minted identifier, not user prose, so reading it
    is identification rather than a judgement about what anyone meant.

    The phase comes from ``hook_event_name`` -- also harness-minted -- so one
    script serves both hook points and the reader can tell a call that started
    from one that finished.
    """
    tool = event.get("tool_name")
    if not isinstance(tool, str) or not tool.strip():
        return None
    phase = START if event.get("hook_event_name") == "PreToolUse" else DONE
    return f"{phase}\t{label_for(tool.strip())}"


def main(argv: list[str] | None = None) -> int:
    destination = os.environ.get(PROGRESS_FILE_ENV)
    if not destination:
        return 0

    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        # Loud in the harness log, inert to the run. A malformed payload means
        # the bridge's shape changed and the hook needs updating -- worth
        # seeing, never worth failing a planning turn over.
        print(f"dsh-progress-hook: could not parse event ({len(raw)} bytes)", file=sys.stderr)
        return 0

    if not isinstance(event, dict):
        print("dsh-progress-hook: event was not an object", file=sys.stderr)
        return 0

    line = step_line(event)
    if line is None:
        return 0

    try:
        # Append-only and opened per call: many hook processes write here
        # concurrently, and a single short line under O_APPEND is not
        # interleaved by the kernel. Nothing reads back, so there is no state
        # to keep consistent -- only lines to add.
        with Path(destination).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print(f"dsh-progress-hook: could not write progress: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
