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


def step_line(event: dict) -> str | None:
    """Render one progress line, or ``None`` if the event names no tool.

    ``tool_name`` is a harness-minted identifier, not user prose, so reading it
    is identification rather than a judgement about what anyone meant.
    """
    tool = event.get("tool_name")
    if not isinstance(tool, str) or not tool.strip():
        return None
    return tool.strip()


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
