"""Enforce the semantic checkpoint and bounded patch retries for one turn."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .progress_events import (
    ProgressPhase,
    ProgressSource,
    ProgressStatus,
    TimeboxProgressEvent,
)
from .validated_timebox_draft import (
    DRAFT_STATE_FILE_ENV,
    claim_plan_apply_attempt,
)

_APPLY_TOOL = "plan_apply"
_PATCH_TOOL = "timebox_patch"
_PROGRESS_FILE_ENV = "FF_DSH_PROGRESS_FILE"
_SESSION_KEY_ENV = "FF_DSH_SESSION_KEY"
_MAX_ATTEMPTS_ENV = "FF_TIMEBOX_PATCH_MAX_ATTEMPTS"


def guard_decision(
    event: dict,
    state_path: str | None,
    *,
    progress_path: str | None = None,
    max_attempts: int,
) -> dict | None:
    tool = event.get("tool_name")
    short_name = tool.rsplit("__", 1)[-1] if isinstance(tool, str) else ""
    if event.get("hook_event_name") != "PreToolUse":
        return None
    if not state_path:
        # Direct/headless harness use has no host-owned turn state. Do not make
        # previewing unavailable there; Slack-owned runs always provide it.
        return None
    if short_name == _PATCH_TOOL and progress_path:
        if _has_skeleton_understanding(progress_path):
            return None
        reason = (
            "Before building the patch, call "
            "mcp__progress__report_skeleton_understanding with the approved "
            "outline, then retry timebox_patch."
        )
        return _deny(reason)
    if short_name != _APPLY_TOOL:
        return None
    attempt = claim_plan_apply_attempt(state_path)
    if attempt <= max_attempts:
        return None
    _record_exhaustion(attempt)
    reason = (
        f"Timebox patch retry budget exhausted after {max_attempts} attempts. "
        "Stop applying patches and report the latest validation problem to Hugo."
    )
    return _deny(reason)


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "decision": "deny",
        "reason": reason,
    }


def _has_skeleton_understanding(progress_path: str) -> bool:
    try:
        lines = Path(progress_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            event = TimeboxProgressEvent.from_json(line)
        except (TypeError, ValueError):
            continue
        if (
            event.phase is ProgressPhase.UNDERSTANDING_SKELETON
            and event.status is ProgressStatus.SUCCEEDED
        ):
            return True
    return False


def _record_exhaustion(attempt: int) -> None:
    destination = os.environ.get(_PROGRESS_FILE_ENV)
    if not destination:
        return
    event = TimeboxProgressEvent(
        session_key=os.environ.get(_SESSION_KEY_ENV) or "unscoped",
        sequence=0,
        source=ProgressSource.RUNTIME,
        phase=ProgressPhase.REVISING_PATCH,
        status=ProgressStatus.FAILED,
        attempt=attempt,
        refusal_code="retry_budget_exhausted",
    )
    try:
        with Path(destination).open("a", encoding="utf-8") as stream:
            stream.write(event.to_json() + "\n")
    except OSError:
        return


def _max_attempts() -> int:
    try:
        return max(1, int(os.environ.get(_MAX_ATTEMPTS_ENV, "5")))
    except ValueError:
        return 5


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        return 0
    if not isinstance(event, dict):
        return 0
    decision = guard_decision(
        event,
        os.environ.get(DRAFT_STATE_FILE_ENV),
        progress_path=os.environ.get(_PROGRESS_FILE_ENV),
        max_attempts=_max_attempts(),
    )
    if decision is not None:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
