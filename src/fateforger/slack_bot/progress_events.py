"""Transport-neutral, privacy-bounded progress facts for a timeboxing turn."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_VERSION = 1
_MAX_SESSION_KEY = 256
_MAX_CODE = 64
_MAX_VIOLATION_KINDS = 16
_CODE = re.compile(r"^[a-z0-9][a-z0-9_:-]*$")


class ProgressSource(StrEnum):
    HARNESS_HOOK = "harness_hook"
    TMBX_MCP = "tmbx_mcp"
    AGENT = "agent"
    RUNTIME = "runtime"


class ProgressPhase(StrEnum):
    PREPARING = "preparing"
    UNDERSTANDING_SKELETON = "understanding_skeleton"
    READING_PLAN = "reading_plan"
    LOADING_CONSTRAINTS = "loading_constraints"
    WEIGHING_OPTIONS = "weighing_options"
    DRAFTING_PATCH = "drafting_patch"
    VALIDATING_PATCH = "validating_patch"
    REVISING_PATCH = "revising_patch"
    AWAITING_APPROVAL = "awaiting_approval"
    COMMITTING = "committing"
    UNDOING = "undoing"
    OTHER = "other"


class ProgressStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ProgressFocus(StrEnum):
    APPROVED_OUTLINE = "approved_outline"
    FIXED_EVENTS = "fixed_events"
    DEEP_WORK = "deep_work"
    SHALLOW_WORK = "shallow_work"
    EXERCISE = "exercise"
    MEALS_BREAKS = "meals_breaks"
    BUFFERS = "buffers"
    WORKDAY_BOUNDARIES = "workday_boundaries"
    DAY_BALANCE = "day_balance"


class ProgressSelection(StrEnum):
    PRESERVE_APPROVED_POSITION = "preserve_approved_position"
    PLACE_EARLIER = "place_earlier"
    PLACE_LATER = "place_later"
    KEEP_FIXED_TIME = "keep_fixed_time"
    SPLIT_AROUND_ANCHOR = "split_around_anchor"
    CONSOLIDATE_BLOCKS = "consolidate_blocks"


class ProgressTradeoff(StrEnum):
    PROTECT_DEEP_WORK = "protect_deep_work"
    REDUCE_FRAGMENTATION = "reduce_fragmentation"
    PRESERVE_ANCHORS = "preserve_anchors"
    HONOR_CONSTRAINTS = "honor_constraints"
    PROTECT_BUFFER = "protect_buffer"
    PROTECT_DURATION = "protect_duration"
    FIT_WORKDAY = "fit_workday"


@dataclass(frozen=True)
class TimeboxProgressEvent:
    """One fixed-field fact safe to serialize, persist, and present."""

    session_key: str
    sequence: int
    source: ProgressSource
    phase: ProgressPhase
    status: ProgressStatus
    attempt: int | None = None
    block_count: int | None = None
    violation_count: int | None = None
    violation_kinds: tuple[str, ...] = ()
    overspecified_count: int | None = None
    refusal_code: str | None = None
    focus: ProgressFocus | None = None
    preserved_count: int | None = None
    remaining_count: int | None = None
    decision_state: str | None = None
    option_count: int | None = None
    selection: ProgressSelection | None = None
    tradeoff: ProgressTradeoff | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_key, str) or not self.session_key.strip():
            raise ValueError("session_key must be a non-empty string")
        if len(self.session_key) > _MAX_SESSION_KEY:
            raise ValueError(f"session_key exceeds {_MAX_SESSION_KEY} characters")
        _validate_count("sequence", self.sequence, minimum=0)
        _validate_optional_count("attempt", self.attempt, minimum=1)
        _validate_optional_count("block_count", self.block_count, minimum=0)
        _validate_optional_count(
            "violation_count", self.violation_count, minimum=0
        )
        _validate_optional_count(
            "overspecified_count", self.overspecified_count, minimum=0
        )
        if not isinstance(self.violation_kinds, tuple):
            raise TypeError("violation_kinds must be a tuple")
        if len(self.violation_kinds) > _MAX_VIOLATION_KINDS:
            raise ValueError(
                f"violation_kinds exceeds {_MAX_VIOLATION_KINDS} entries"
            )
        for kind in self.violation_kinds:
            _validate_code("violation_kinds", kind)
        if self.refusal_code is not None:
            _validate_code("refusal_code", self.refusal_code)
        _validate_optional_enum("focus", self.focus, ProgressFocus)
        _validate_optional_count(
            "preserved_count", self.preserved_count, minimum=0
        )
        _validate_optional_count(
            "remaining_count", self.remaining_count, minimum=0
        )
        if self.decision_state is not None and self.decision_state not in {
            "opened",
            "selected",
            "revised",
            "closed",
        }:
            raise ValueError("decision_state must be a supported state")
        _validate_optional_count("option_count", self.option_count, minimum=0)
        _validate_optional_enum("selection", self.selection, ProgressSelection)
        _validate_optional_enum("tradeoff", self.tradeoff, ProgressTradeoff)

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "version": _VERSION,
            "session_key": self.session_key,
            "sequence": self.sequence,
            "source": self.source.value,
            "phase": self.phase.value,
            "status": self.status.value,
        }
        for name in (
            "attempt",
            "block_count",
            "violation_count",
            "overspecified_count",
            "refusal_code",
            "focus",
            "preserved_count",
            "remaining_count",
            "decision_state",
            "option_count",
            "selection",
            "tradeoff",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.violation_kinds:
            payload["violation_kinds"] = list(self.violation_kinds)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> TimeboxProgressEvent:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("progress event must be a JSON object")
        allowed = {
            "version",
            "session_key",
            "sequence",
            "source",
            "phase",
            "status",
            "attempt",
            "block_count",
            "violation_count",
            "violation_kinds",
            "overspecified_count",
            "refusal_code",
            "focus",
            "preserved_count",
            "remaining_count",
            "decision_state",
            "option_count",
            "selection",
            "tradeoff",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"unknown progress fields: {', '.join(unknown)}")
        if payload.get("version") != _VERSION:
            raise ValueError("unsupported progress event version")
        kinds = payload.get("violation_kinds", ())
        if not isinstance(kinds, (list, tuple)):
            raise TypeError("violation_kinds must be an array")
        try:
            return cls(
                session_key=payload["session_key"],
                sequence=payload["sequence"],
                source=ProgressSource(payload["source"]),
                phase=ProgressPhase(payload["phase"]),
                status=ProgressStatus(payload["status"]),
                attempt=payload.get("attempt"),
                block_count=payload.get("block_count"),
                violation_count=payload.get("violation_count"),
                violation_kinds=tuple(kinds),
                overspecified_count=payload.get("overspecified_count"),
                refusal_code=payload.get("refusal_code"),
                focus=_optional_enum(payload.get("focus"), ProgressFocus),
                preserved_count=payload.get("preserved_count"),
                remaining_count=payload.get("remaining_count"),
                decision_state=payload.get("decision_state"),
                option_count=payload.get("option_count"),
                selection=_optional_enum(payload.get("selection"), ProgressSelection),
                tradeoff=_optional_enum(payload.get("tradeoff"), ProgressTradeoff),
            )
        except KeyError as exc:
            raise ValueError(f"missing progress field: {exc.args[0]}") from exc


def _validate_count(name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _validate_optional_count(
    name: str, value: object | None, *, minimum: int
) -> None:
    if value is not None:
        _validate_count(name, value, minimum=minimum)


def _validate_code(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_CODE
        or _CODE.fullmatch(value) is None
    ):
        raise ValueError(
            f"{name} must be a stable lowercase code of at most {_MAX_CODE} characters"
        )


def _validate_optional_enum(
    name: str, value: object | None, enum_type: type[StrEnum]
) -> None:
    if value is not None and not isinstance(value, enum_type):
        raise TypeError(f"{name} must be a {enum_type.__name__}")


def _optional_enum(value: object, enum_type: type[StrEnum]):
    if value is None:
        return None
    return enum_type(value)
