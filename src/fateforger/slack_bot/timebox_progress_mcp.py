"""A tiny model-facing MCP facade for bounded timebox progress facts.

Patching and reporting deliberately stay separate. The planner may report its
understanding of an already-approved skeleton or a material scheduling choice;
attempt counts, validation failures, and committable state come from tmbx tool
results instead and cannot be self-reported here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .progress_events import (
    ProgressPhase,
    ProgressSource,
    ProgressStatus,
    TimeboxProgressEvent,
)

logger = logging.getLogger(__name__)

_PROGRESS_FILE_ENV = "FF_DSH_PROGRESS_FILE"
_SESSION_KEY_ENV = "FF_DSH_SESSION_KEY"
_RECORDED = "Progress recorded. Continue the timebox work."
_IGNORED = "Progress ignored. Continue the timebox work."

mcp = FastMCP(
    name="timebox-progress",
    instructions=(
        "Report bounded conclusions about timeboxing work, never private reasoning. "
        "Patching and validation use the tmbx tools; these tools only keep the user informed."
    ),
)


@mcp.tool(name="report_skeleton_understanding")
def report_skeleton_understanding(
    focus: str,
    preserved_count: int,
    remaining_count: int,
) -> str:
    """Report once after understanding the approved skeleton.

    ``focus`` is one short description of the current transformation, using
    only information already present in the approved outline. Do not include
    reasoning, hidden alternatives, raw calendar payloads, or instructions.
    """

    try:
        event = TimeboxProgressEvent(
            session_key=_session_key(),
            sequence=0,
            source=ProgressSource.AGENT,
            phase=ProgressPhase.UNDERSTANDING_SKELETON,
            status=ProgressStatus.SUCCEEDED,
            focus=_bounded(focus, name="focus"),
            preserved_count=preserved_count,
            remaining_count=remaining_count,
        )
    except (TypeError, ValueError):
        return _IGNORED
    _append(event)
    return _RECORDED


@mcp.tool(name="report_scheduling_decision")
def report_scheduling_decision(
    decision_state: Literal["opened", "selected", "revised", "closed"],
    focus: str,
    option_count: int,
    selection: str | None = None,
    tradeoff: str | None = None,
) -> str:
    """Report only a material scheduling choice visible to the user.

    Do not invent options to create progress. Report an ``opened`` decision
    when two or more genuinely viable placements remain, then ``selected``,
    ``revised``, or ``closed`` only when that same choice changes state.
    """

    if decision_state in {"selected", "revised", "closed"} and not selection:
        return _IGNORED
    try:
        event = TimeboxProgressEvent(
            session_key=_session_key(),
            sequence=0,
            source=ProgressSource.AGENT,
            phase=ProgressPhase.WEIGHING_OPTIONS,
            status=ProgressStatus.SUCCEEDED,
            focus=_bounded(focus, name="focus"),
            decision_state=decision_state,
            option_count=option_count,
            selection=_bounded(selection, name="selection") if selection else None,
            tradeoff=_bounded(tradeoff, name="tradeoff") if tradeoff else None,
        )
    except (TypeError, ValueError):
        return _IGNORED
    _append(event)
    return _RECORDED


def _session_key() -> str:
    return os.environ.get(_SESSION_KEY_ENV) or "unscoped"


def _bounded(value: str, *, name: str) -> str:
    collapsed = " ".join(value.split())
    if not collapsed or len(collapsed) > 160:
        raise ValueError(f"{name} must be between 1 and 160 characters")
    return collapsed


def _append(event: TimeboxProgressEvent) -> None:
    destination = os.environ.get(_PROGRESS_FILE_ENV)
    if not destination:
        return
    try:
        path = Path(destination)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(event.to_json() + "\n")
    except OSError as exc:
        logger.warning(
            "timebox progress delivery failed error_type=%s",
            type(exc).__name__,
        )


if __name__ == "__main__":
    mcp.run()
