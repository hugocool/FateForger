"""Bind a calendar commit to the exact candidate most recently validated.

DeepSeek hooks run in separate short-lived processes, so the owning harness
turn carries this tiny state through a private file in its temporary workspace.
The file stores only a canonical digest: snapshots, patches, calendar content,
and model text never become progress or log payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

DRAFT_STATE_FILE_ENV = "FF_DSH_DRAFT_STATE_FILE"
CANDIDATE_OUTPUT_FILE_ENV = "FF_DSH_CANDIDATE_OUTPUT_FILE"

_APPLY_TOOL = "plan_apply"
_READ_TOOL = "plan_read"
_VERSION = 1


def record_validation_result(
    event: dict[str, Any],
    destination: str | None,
    candidate_destination: str | None = None,
) -> None:
    """Remember a committable apply result; clear state for a newer attempt/read."""

    if not destination:
        return
    tool = event.get("tool_name")
    short_name = tool.rsplit("__", 1)[-1] if isinstance(tool, str) else ""
    phase = event.get("hook_event_name")

    if short_name == _READ_TOOL:
        # A fresh snapshot invalidates the candidate digest, but it is not a
        # fresh harness turn. Preserve the apply count so re-reading cannot
        # route around the bounded retry budget.
        _write(
            destination,
            {"version": _VERSION, "attempts": current_attempt(destination)},
        )
        _clear(candidate_destination)
        return
    if short_name != _APPLY_TOOL or phase != "PostToolUse":
        return

    response = _object(event.get("tool_response"))
    tool_input = event.get("tool_input")
    digest = _candidate_digest(tool_input)
    attempts = current_attempt(destination)
    if response.get("ok") is not True or response.get("committable") is not True or not digest:
        _write(destination, {"version": _VERSION, "attempts": attempts})
        _clear(candidate_destination)
        return
    _write(
        destination,
        {"version": _VERSION, "attempts": attempts, "digest": digest},
    )
    if isinstance(tool_input, dict):
        _write(
            candidate_destination,
            {
                "version": _VERSION,
                "digest": digest,
                "snapshot": tool_input["snapshot"],
                "patch": tool_input["patch"],
                "rendered": response.get("rendered", ""),
            },
        )


def read_validated_candidate(source: str | Path | None):
    """Load a private candidate export, validating its canonical digest."""

    if source is None:
        return None
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != _VERSION:
        return None
    candidate_input = {
        "snapshot": payload.get("snapshot"),
        "patch": payload.get("patch"),
    }
    rendered = payload.get("rendered")
    if not isinstance(rendered, str) or not rendered.strip():
        return None
    digest = _candidate_digest(candidate_input)
    expected = payload.get("digest")
    if not isinstance(expected, str) or not isinstance(digest, str):
        return None
    if not hmac.compare_digest(expected, digest):
        return None
    from .timebox_candidate import ValidatedTimeboxCandidate

    return ValidatedTimeboxCandidate(
        digest=digest,
        snapshot=candidate_input["snapshot"],
        patch=candidate_input["patch"],
        rendered=rendered,
    )


def claim_plan_apply_attempt(destination: str | None) -> int:
    """Increment and return the runtime-owned apply-attempt counter."""

    if not destination:
        return 0
    attempt = current_attempt(destination) + 1
    _write(destination, {"version": _VERSION, "attempts": attempt})
    return attempt


def current_attempt(source: str | None) -> int:
    if not source:
        return 0
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return 0
    value = payload.get("attempts") if isinstance(payload, dict) else None
    return value if isinstance(value, int) and value >= 0 else 0


def validated_commit_matches(event: dict[str, Any], source: str | None) -> bool:
    """Return whether ``event`` commits the exact last committable candidate."""

    if not source:
        return False
    try:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("version") != _VERSION:
        return False
    expected = payload.get("digest")
    if not isinstance(expected, str):
        return False
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict) and tool_input.get("expect", "clean") != "clean":
        return False
    actual = _candidate_digest(tool_input)
    return isinstance(actual, str) and hmac.compare_digest(actual, expected)


def consume_validated_draft(destination: str | None) -> bool:
    """Spend a validated candidate exactly once."""

    if not destination:
        return False
    try:
        Path(destination).write_text("", encoding="utf-8")
    except OSError:
        return False
    return True


def _candidate_digest(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("snapshot"), dict) or not isinstance(
        value.get("patch"), dict
    ):
        return None
    candidate = {"snapshot": value["snapshot"], "patch": value["patch"]}
    try:
        encoded = json.dumps(
            candidate,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(destination: str | None, payload: dict[str, object]) -> None:
    if not destination:
        return
    try:
        Path(destination).write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        return


def _clear(destination: str | None) -> None:
    if not destination:
        return
    try:
        Path(destination).write_text("", encoding="utf-8")
    except OSError:
        return
