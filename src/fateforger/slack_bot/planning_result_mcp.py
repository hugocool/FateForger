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

from collections.abc import Iterable, Mapping

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from pydantic import Field as PydanticField
from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    BlockerOption,
    PlannerAssumptionDraft,
    PlanningResult,
    UserBlockerDraft,
)

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


class AssumptionInput(BaseModel):
    """One placement the planner decided for itself.

    Deliberately a model rather than a bare dict. These arguments were typed
    `list[dict[str, Any]]`, so the tool schema showed arrays of unconstrained
    objects while the server validated strict contracts requiring these exact
    names -- and the refusal stripped them. Measured over 31 draws: 4-11 failed
    submissions per turn, after which the planner read the host's own source to
    recover the field names, at 110-119s per candidate turn. A tool argument
    whose shape the caller cannot see is one it will get wrong.

    Loose on purpose: the strict contract still validates in `_validated`. This
    exists to be *described*, not to be the gate.
    """

    requirement_id: str = PydanticField(
        description="The exact requirement id from the brief's readiness gaps."
    )
    value: Any = PydanticField(description="What was decided, e.g. a time or range.")
    why_needed: str = PydanticField(description="One line: why this had to be decided.")
    invalidated_by: list[str] = PydanticField(
        default_factory=list,
        description="Requirement ids whose change would retire this assumption.",
    )


class BlockerInput(BaseModel):
    """One decision that is genuinely the user's to make."""

    requirement_id: str = PydanticField(
        description="The exact requirement id from the brief's readiness gaps."
    )
    why_needed: str = PydanticField(description="One line: why the user must decide.")


class BlockerOptionInput(BaseModel):
    """One concrete alternative offered against a blocker."""

    label: str = PydanticField(description="What the user reads on the button.")
    effect: str = PydanticField(description="One line: what choosing it does.")


def _as_dicts(items: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalise tool arguments to plain mappings, whichever shape arrived."""

    return [
        dict(item)
        if isinstance(item, Mapping)
        else item.model_dump(mode="json")
        for item in items
    ]


@mcp.tool(name="submit_planning_result")
def submit_planning_result(
    target_artifact: Literal[
        "day_frame",
        "captured_inputs",
        "skeleton",
        "validated_candidate",
    ],
    artifact: dict[str, Any] | None,
    assumptions: list[AssumptionInput],
    blockers: list[BlockerInput],
    blocker_options: list[BlockerOptionInput] | None = None,
) -> str:
    """Record this turn's result. Call it exactly once, at the end.

    ``target_artifact`` is the kind the host named in the brief; it is not
    yours to choose. ``artifact`` is that artifact's payload. Every ordinary
    placement you decided yourself belongs in ``assumptions``, each naming the
    requirement it settles and what would invalidate it.

    **For ``validated_candidate``: call ``plan_apply`` before this.** The patch
    that call produces is the only thing that can be committed afterwards, and
    the host takes it from the call itself -- it cannot accept one written into
    ``artifact``. A candidate submitted without applying is shown to the user,
    approved by them, and then found to be uncommittable, so this call is
    refused rather than allowed to reach them.

    ``blockers`` is only for a decision that is genuinely the user's, and it
    replaces the artifact rather than accompanying it: an artifact asks to be
    approved and a blocker asks a question, and one turn shows the user one of
    those. Submitting neither ends the turn with nothing to review.

    ``blocker_options`` offers the user concrete alternatives to that one
    question, as ``{"label": ..., "effect": ...}`` -- what they read, and what
    one line says choosing it does. Send it only where the answer set is
    genuinely closed and you can name every alternative: two to four ways three
    unallocated hours could be spent, not four guesses at what the day is for.
    **Omit it otherwise.** A question with no options reaches the user as a text
    box, which is the right answer for a question with no closed answer set --
    four invented choices would hide the fifth answer they actually had.
    Options attach to the single blocker in ``blockers``, and you do not name
    them: the host mints each identifier.
    """

    destination = _destination()
    # FastMCP hands these over as models; an in-process caller passes mappings.
    # Both are accepted because the strict contract below is the gate, and a
    # boundary that refused one shape would only move the failure earlier
    # without making anything safer.
    assumptions = _as_dicts(assumptions)  # type: ignore[assignment]
    blockers = _as_dicts(blockers)  # type: ignore[assignment]
    if blocker_options is not None:
        blocker_options = _as_dicts(blocker_options)  # type: ignore[assignment]
    document = _validated(
        target_artifact=target_artifact,
        artifact=artifact,
        assumptions=assumptions,
        blockers=blockers,
        blocker_options=blocker_options,
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
    blocker_options: list[dict[str, Any]] | None = None,
) -> PlanningResult:
    if blocker_options and len(blockers) != 1:
        raise PlanningResultRefused(
            "options belong to one question, and this submission does not have "
            "exactly one. Submit the blocker they answer, with its options."
        )
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
    offered = _minted(blocker_options)
    if offered:
        blockers = [{**blockers[0], "options": offered}]
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
            # Canonical, so the comparison below is about content rather than
            # the order a dict happened to serialise in. Without it a retry
            # that re-encodes the same result with different key order is read
            # as a second, differing submission and refused -- which is exactly
            # the case the idempotent path exists to allow. List order stays
            # significant: a list is ordered, and collapsing two orderings
            # would let a genuinely different submission through.
            sort_keys=True,
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


def _minted(blocker_options: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Name each offered choice, host-side, and refuse a planner that named it.

    ``option_id`` is the value a later press is checked against, so it has to be
    the host's to mint: an identifier the planner chose is one it could point at
    a different choice than the user read, or reuse across two choices and make
    the press ambiguous. Overwriting a supplied one silently would be worse than
    refusing, because the planner would go on believing it had named the choice.

    The position, not a fresh uuid. A retried submission is compared to the one
    already recorded byte for byte, so identifiers drawn fresh would read one
    submission sent twice as two different results and refuse the second --
    which is the case the idempotent path exists to allow.
    """

    if not blocker_options:
        return []
    try:
        if any("option_id" in option for option in blocker_options):
            raise PlanningResultRefused(
                "option identifiers belong to the host, not to this submission. "
                "Resubmit each option with only its label and its effect."
            )
        return [
            {**option, "option_id": f"option-{index}"}
            for index, option in enumerate(blocker_options, start=1)
        ]
    except TypeError as exc:
        raise PlanningResultRefused(
            "each option must be an object carrying a label and an effect. "
            "Resubmit the options in that shape."
        ) from exc


def _known_field_names() -> frozenset[str]:
    """Every field name in the contracts this tool validates against.

    A loc segment matching one of these is a name this system minted, so it can
    be repeated back. Anything else is a key the model invented.
    """

    models = (
        PlanningResult,
        ArtifactDraft,
        PlannerAssumptionDraft,
        UserBlockerDraft,
        BlockerOption,
    )
    return frozenset(
        name for model in models for name in model.model_fields
    )


_KNOWN_FIELDS = _known_field_names()


def _shape_codes(exc: ValidationError) -> str:
    """Name the failing field path and the error code, and nothing else.

    This used to report only `loc[0]`, so a missing `requirement_id` inside an
    assumption came back as `assumptions:missing` -- true, and useless. Measured
    over 31 planner draws: 4-11 failed submissions per turn, after which the
    planner read the host's own source to recover the names it was never told.

    The path is safe to repeat because every segment is either an index or a
    field name this system declared. A segment that is neither is a key the
    model invented, and it is replaced rather than echoed -- Pydantic's own
    message quotes the offending input, which is model-authored text on a path
    that ends in the host's logs.
    """

    codes = set()
    for error in exc.errors():
        parts = [
            str(segment)
            if isinstance(segment, int) or segment in _KNOWN_FIELDS
            else "<unknown-field>"
            for segment in error["loc"]
        ] or ["result"]
        codes.add(f"{'.'.join(parts)}:{error['type']}")
    return ", ".join(sorted(codes))


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
