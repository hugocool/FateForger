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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .validated_timebox_draft import (
    DRAFT_STATE_FILE_ENV,
    current_attempt,
    record_validation_result,
)

#: Set per-run by ``harness_bridge``. Absent means nobody is listening — an
#: ordinary headless run rather than a Slack turn — so there is nothing to do.
PROGRESS_FILE_ENV = "FF_DSH_PROGRESS_FILE"

#: Where a committed transaction id is recorded, so Slack can offer to reverse
#: it. Separate from the progress file because the two have different readers
#: and different lifetimes: progress is consumed as it streams, a transaction
#: id outlives the turn and is read once at the end.
COMMIT_FILE_ENV = "FF_DSH_COMMIT_FILE"

#: The tool whose result carries a reversible transaction. Matched by suffix
#: because the harness namespaces MCP tools as ``mcp__<server>__<name>`` --
#: identifiers this system minted, not anything a person wrote.
_COMMIT_TOOL = "plan_commit"
_REPORT_TOOLS = frozenset(
    {"report_skeleton_understanding", "report_scheduling_decision"}
)

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


class ProgressPhase(str, Enum):
    READING_PLAN = "reading_plan"
    LOADING_CONSTRAINTS = "loading_constraints"
    DRAFTING_PATCH = "drafting_patch"
    VALIDATING_PATCH = "validating_patch"
    REVISING_PATCH = "revising_patch"
    COMMITTING = "committing"
    UNDOING = "undoing"
    OTHER = "other"


class ProgressStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ProgressEvent:
    """One bounded fact safe to persist and project into a Slack card."""

    phase: ProgressPhase
    status: ProgressStatus
    safe_detail: dict[str, Any] = field(default_factory=dict)

    def to_line(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "phase": self.phase.value,
                "status": self.status.value,
                "safe_detail": self.safe_detail,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_line(cls, line: str) -> ProgressEvent:
        payload = json.loads(line)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("unsupported progress event")
        detail = payload.get("safe_detail")
        if not isinstance(detail, dict):
            raise TypeError("progress safe_detail must be an object")
        return cls(
            phase=ProgressPhase(payload["phase"]),
            status=ProgressStatus(payload["status"]),
            safe_detail=detail,
        )


_PHASES = {
    "plan_read": ProgressPhase.READING_PLAN,
    "plan_apply": ProgressPhase.DRAFTING_PATCH,
    "plan_commit": ProgressPhase.COMMITTING,
    "plan_undo": ProgressPhase.UNDOING,
    "memory_get_active_constraints": ProgressPhase.LOADING_CONSTRAINTS,
    "memory_get_suspended_constraints": ProgressPhase.LOADING_CONSTRAINTS,
    "memory_get_session_constraints": ProgressPhase.LOADING_CONSTRAINTS,
}


def progress_event(event: dict, *, attempt: int | None = None) -> ProgressEvent | None:
    """Project one hook envelope into allow-listed, user-safe progress."""

    tool = event.get("tool_name")
    if not isinstance(tool, str) or not tool.strip():
        return None
    short_name = tool.rsplit("__", 1)[-1]
    if short_name in _REPORT_TOOLS:
        # The reporter MCP writes its bounded semantic event directly. Projecting
        # the tool lifecycle too would add a duplicate generic "Working" row.
        return None
    phase = _PHASES.get(short_name, ProgressPhase.OTHER)
    if event.get("hook_event_name") == "PreToolUse":
        return ProgressEvent(
            phase=phase,
            status=ProgressStatus.STARTED,
            safe_detail={"attempt": attempt} if attempt else {},
        )

    raw_response = event.get("tool_response")
    if not isinstance(raw_response, str):
        return ProgressEvent(phase=phase, status=ProgressStatus.SUCCEEDED)
    response = _response_object(raw_response)
    if short_name == "plan_read":
        succeeded = response.get("ok") is True
        blocks = response.get("blocks")
        detail = (
            {"block_count": len(blocks)}
            if succeeded and isinstance(blocks, list)
            else {}
        )
        if not succeeded and isinstance(response.get("reason"), str):
            detail = {"refusal_reason": response["reason"]}
        return ProgressEvent(
            phase=phase,
            status=ProgressStatus.SUCCEEDED if succeeded else ProgressStatus.FAILED,
            safe_detail=detail,
        )

    if short_name == "plan_apply":
        if response.get("ok") is not True:
            reason = response.get("reason")
            detail = {"refusal_reason": reason} if isinstance(reason, str) else {}
            if attempt:
                detail["attempt"] = attempt
            return ProgressEvent(
                phase=ProgressPhase.REVISING_PATCH,
                status=ProgressStatus.FAILED,
                safe_detail=detail,
            )
        violations = response.get("violations")
        violation_rows = violations if isinstance(violations, list) else []
        if response.get("committable") is not True:
            kinds = sorted(
                {
                    row.get("kind")
                    for row in violation_rows
                    if isinstance(row, dict) and isinstance(row.get("kind"), str)
                }
            )
            detail = {
                "violation_count": len(violation_rows),
                "violation_kinds": kinds,
            }
            if attempt:
                detail["attempt"] = attempt
            return ProgressEvent(
                phase=ProgressPhase.REVISING_PATCH,
                status=ProgressStatus.FAILED,
                safe_detail=detail,
            )
        overspecified = response.get("overspecified")
        detail = {
            "overspecified_count": len(overspecified)
            if isinstance(overspecified, list)
            else 0
        }
        block_count = response.get("block_count")
        if isinstance(block_count, int) and not isinstance(block_count, bool):
            detail["block_count"] = block_count
        if attempt:
            detail["attempt"] = attempt
        return ProgressEvent(
            phase=ProgressPhase.VALIDATING_PATCH,
            status=ProgressStatus.SUCCEEDED,
            safe_detail=detail,
        )

    if short_name == "plan_commit":
        committed = response.get("committed") is True
        detail: dict[str, Any] = {}
        if not committed and isinstance(response.get("reason"), str):
            detail["refusal_reason"] = response["reason"]
        conflicts = response.get("conflicts")
        if isinstance(conflicts, list):
            detail["conflict_count"] = len(conflicts)
        return ProgressEvent(
            phase=phase,
            status=ProgressStatus.SUCCEEDED if committed else ProgressStatus.FAILED,
            safe_detail=detail,
        )

    return ProgressEvent(phase=phase, status=ProgressStatus.SUCCEEDED)


def _response_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def committed_tx_id(event: dict) -> str | None:
    """The transaction id from a ``plan_commit`` result, if this is one.

    Read structurally out of the JSON tmbx returned, never scraped from prose:
    ``plan_commit`` answers ``{"committed": true, "tx_id": ...}``, so the id is
    a field lookup. A refused commit carries no ``tx_id`` and yields ``None``,
    which is what stops Slack offering to reverse a write that never happened.
    """
    tool = event.get("tool_name")
    if not isinstance(tool, str) or not tool.endswith(_COMMIT_TOOL):
        return None
    response = event.get("tool_response")
    if not isinstance(response, str):
        return None
    try:
        payload = json.loads(response)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("committed"):
        return None
    tx_id = payload.get("tx_id")
    return tx_id if isinstance(tx_id, str) and tx_id.strip() else None


def main(argv: list[str] | None = None) -> int:
    destination = os.environ.get(PROGRESS_FILE_ENV)
    draft_state = os.environ.get(DRAFT_STATE_FILE_ENV)
    commit_destination = os.environ.get(COMMIT_FILE_ENV)
    if not destination and not draft_state and not commit_destination:
        return 0

    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        # Loud in the harness log, inert to the run. A malformed payload means
        # the bridge's shape changed and the hook needs updating -- worth
        # seeing, never worth failing a planning turn over.
        print(
            f"dsh-progress-hook: could not parse event ({len(raw)} bytes)",
            file=sys.stderr,
        )
        return 0

    if not isinstance(event, dict):
        print("dsh-progress-hook: event was not an object", file=sys.stderr)
        return 0

    record_validation_result(event, draft_state)

    tx_id = committed_tx_id(event)
    if tx_id:
        _append(commit_destination, tx_id, what="commit id")

    if not destination:
        return 0

    tool = event.get("tool_name")
    short_name = tool.rsplit("__", 1)[-1] if isinstance(tool, str) else ""
    attempt = current_attempt(draft_state) if short_name == "plan_apply" else None
    projected = progress_event(event, attempt=attempt)
    if projected is None:
        return 0

    _append(destination, projected.to_line())
    return 0


def _append(destination: str | None, line: str, *, what: str = "progress") -> None:
    """Add one line, or do nothing when nobody is listening.

    ``what`` names the kind of record, because this now serves two files with
    different readers -- a failure that says only "could not write" leaves the
    reader guessing which one, and they fail for different reasons.

    Append-only and opened per call: many hook processes write concurrently,
    and a single short line under O_APPEND is not interleaved by the kernel.
    Nothing reads back, so there is no state to keep consistent.
    """
    if not destination:
        return
    try:
        with Path(destination).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print(
            f"dsh-progress-hook: could not write {what} to {destination}: {exc}",
            file=sys.stderr,
        )


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
