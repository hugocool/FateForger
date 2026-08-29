"""The model-facing facade for the one typed result a planning turn owes.

Progress and results look alike and are not. A progress line that never lands
costs the user a status update, so ``timebox_progress_mcp`` swallows its own
failures. A planning result that never lands costs the turn: the kernel's whole
promise is that an advance produces the next reviewable artifact, and a planner
that explained itself in prose and submitted nothing has produced nothing. So
every refusal here is loud, and stdout can never stand in for a submission --
the host reads this file, not the transcript.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import PlanningResult

#: Where the host tells this server to write. Restated rather than imported
#: from ``harness_bridge``: that module is the Slack host, and pulling it into
#: this child would drag the whole bot import graph into a one-tool server.
#: ``test_planning_result_mcp`` asserts the two names are equal, because a
#: drift here would present as a planner submitting into nowhere.
PLANNING_RESULT_FILE_ENV = "FF_DSH_PLANNING_RESULT_FILE"

_RECORDED = "Planning result recorded. End this turn."


class PlanningResultRefused(ValueError):
    """This submission was not recorded, and the planner is told so.

    Raised rather than logged. Returning a polite string would leave the model
    believing the turn's only deliverable had landed, and the host discovering
    otherwise after the process had exited and could no longer be asked.
    """


mcp = FastMCP(
    name="planning-result",
    instructions=(
        "Submit the one artifact this planning turn was asked for. The host "
        "reads only what this tool records; prose in your final message is "
        "presentation and records nothing."
    ),
)


@mcp.tool(name="submit_planning_result")
def submit_planning_result(
    target_artifact: Literal[
        "day_frame",
        "captured_inputs",
        "skeleton",
        "validated_candidate",
    ],
    artifact: dict[str, Any] | None,
    assumptions: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> str:
    """Record this turn's result. Call it exactly once, at the end.

    ``target_artifact`` is the kind the host named in the brief; it is not
    yours to choose. ``artifact`` is that artifact's payload. Every ordinary
    placement you decided yourself belongs in ``assumptions``, each naming the
    requirement it settles and what would invalidate it.

    ``blockers`` is only for a decision that is genuinely the user's, and it
    replaces the artifact rather than accompanying it: an artifact asks to be
    approved and a blocker asks a question, and one turn shows the user one of
    those. Submitting neither ends the turn with nothing to review.
    """

    destination = _destination()
    document = _validated(
        target_artifact=target_artifact,
        artifact=artifact,
        assumptions=assumptions,
        blockers=blockers,
    ).model_dump_json()

    recorded = _recorded(destination)
    if recorded is not None:
        if recorded == document:
            # A retried tool call is a transport event, not a change of mind.
            return _RECORDED
        raise PlanningResultRefused(
            "this turn already recorded a different planning result. One "
            "advance produces one artifact, so the first submission stands."
        )

    _write(destination, document)
    return _RECORDED


def _destination() -> Path:
    configured = os.environ.get(PLANNING_RESULT_FILE_ENV, "").strip()
    if not configured:
        # The progress server treats an unset file as "nobody is listening" and
        # returns. Here the same silence would let the planner believe it had
        # delivered the turn, so an unconfigured host is a failure of the host.
        raise PlanningResultRefused(
            "this host provisioned no planning-result file, so nothing this "
            "turn produces can be recorded. Say that plainly and stop."
        )
    return Path(configured)


def _validated(
    *,
    target_artifact: str,
    artifact: dict[str, Any] | None,
    assumptions: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> PlanningResult:
    if artifact is not None and blockers:
        raise PlanningResultRefused(
            "an artifact and a blocker cannot both be this turn's result. "
            "Submit the artifact, or the blocker that stopped you making it."
        )
    if artifact is None and not blockers:
        raise PlanningResultRefused(
            "this submission carries neither an artifact nor a blocker, so the "
            "turn would end with nothing for the user to review."
        )

    updates = (
        [] if artifact is None else [{"kind": target_artifact, "payload": artifact}]
    )
    try:
        # Through JSON deliberately. These arguments crossed MCP as JSON, the
        # contracts validate strictly, and strict Python-mode validation would
        # reject the plain strings the wire actually carries.
        document = json.dumps(
            {
                "artifact_updates": updates,
                "assumptions": assumptions,
                "blockers": blockers,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise PlanningResultRefused(
            "this submission is not representable as JSON, so it cannot be "
            "recorded. Resubmit using only JSON values."
        ) from exc

    try:
        return PlanningResult.model_validate_json(document)
    except ValidationError as exc:
        raise PlanningResultRefused(
            "this submission does not match the required shape "
            f"({_shape_codes(exc)}). Resubmit one result whose fields match "
            "this tool's declared arguments."
        ) from exc


def _shape_codes(exc: ValidationError) -> str:
    """Name the failing top-level field and the error code, and nothing else.

    Pydantic's own message quotes the offending input, and for an undeclared
    field it quotes the key the model invented. Both are model-authored text on
    a path that ends in the host's logs, so only the two identifiers this
    system minted survive.
    """

    codes = {
        f"{error['loc'][0] if error['loc'] else 'result'}:{error['type']}"
        for error in exc.errors()
    }
    return ", ".join(sorted(str(code) for code in codes))


def _recorded(destination: Path) -> str | None:
    """Return what this turn already recorded, if anything.

    The host provisions one empty file per turn, so the file *is* the turn's
    memory. Keeping it here rather than in a module global means a restarted
    child cannot forget a submission and overwrite it.
    """

    try:
        existing = destination.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return existing or None


def _write(destination: Path, document: str) -> None:
    """Stage beside the destination, then arrive in one rename.

    The host reads this file after the child has exited and cannot ask again,
    so a half-written document would present as a planner that produced
    nothing. Staging in the same directory keeps the rename on one filesystem,
    which is what makes it atomic.
    """

    staging = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    try:
        staging.write_text(document, encoding="utf-8")
        os.replace(staging, destination)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise PlanningResultRefused(
            "this planning result could not be written to the host's file, so "
            "the turn has recorded nothing. Say that plainly and stop."
        ) from exc


if __name__ == "__main__":
    mcp.run()


__all__ = [
    "PLANNING_RESULT_FILE_ENV",
    "PlanningResultRefused",
    "mcp",
    "submit_planning_result",
]
