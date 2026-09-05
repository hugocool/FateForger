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

from fateforger.agents.timeboxing.required_blocks import slugs_on_candidate
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    BlockerOption,
    PlannerAssumptionDraft,
    PlanningResult,
    SkeletonPayload,
    UserBlockerDraft,
)

from .validated_timebox_draft import CANDIDATE_OUTPUT_FILE_ENV

#: Where the host publishes the requirement ids a planner may settle this turn.
#: Written by the bridge from the same readiness report the brief carries, so
#: this server and the kernel cannot disagree about what was open.
OPEN_REQUIREMENTS_FILE_ENV = "FF_DSH_OPEN_REQUIREMENTS_FILE"

#: A JSON list of the registry slugs the day's rules require, written by the
#: host per planning turn so the submit tool can refuse a candidate that lacks
#: one while the planner still has steps left to add it (#214).
REQUIRED_BLOCKS_FILE_ENV = "FF_DSH_REQUIRED_BLOCKS_FILE"

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
    continuation: dict[str, Any] | None = None,
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

    ``continuation`` is for when you cannot finish this turn but have not
    failed: ``{"reason": "..."}``, saying what is left and what you already
    know. Whatever you produced is kept and you resume next turn from that
    reason, so write it for yourself. Use it when a retry budget runs out
    mid-fix -- not as a way to avoid deciding something that is yours to decide.

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
        continuation=continuation,
    ).model_dump_json()

    _refuse_unknown_requirement(assumptions=assumptions)
    _refuse_unapplied_candidate(target_artifact=target_artifact, artifact=artifact)
    _refuse_missing_required_block(target_artifact=target_artifact, artifact=artifact)

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


def _refuse_unknown_requirement(*, assumptions: Any) -> None:
    """Refuse an assumption naming a requirement not open this turn.

    An assumption is the planner's receipt for settling something it was
    entitled to settle. Naming a requirement that is not open -- another
    stage's vocabulary -- makes the receipt unmatchable, and the kernel
    discovers that only after the harness has exited, when nothing can be done
    but drop it (9018f41).

    Checking here puts it inside the turn, where every other check in this
    system already lives: `plan_apply` returns violations and the planner
    re-patches, and the captured-patch guard above refuses and the planner
    applies. The refusal names the ids that would have been accepted, because a
    refusal the model cannot act on only burns a step.

    Fails **open** when the host publishes nothing, unlike the captured-patch
    guard. A host with no candidate file cannot commit at all, so refusing there
    is right; here the kernel still validates after the turn, so the only loss
    is the early correction -- and refusing would break every submission on a
    host that simply does not publish its requirements.
    """

    configured = os.environ.get(OPEN_REQUIREMENTS_FILE_ENV, "").strip()
    if not configured:
        return
    try:
        published = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(published, list):
        return
    allowed = {str(item) for item in published}
    if not allowed:
        return

    for assumption in assumptions or []:
        named = str((assumption or {}).get("requirement_id") or "")
        if named in allowed:
            continue
        raise PlanningResultRefused(
            f"`{named}` is not a requirement this turn can settle, so an "
            "assumption naming it records nothing. The ones open to you now "
            f"are: {', '.join(sorted(allowed))}. Name one of those, or drop "
            "the assumption and submit the artifact on its own."
        )


def _refuse_unapplied_candidate(*, target_artifact: str, artifact: Any) -> None:
    """A candidate nobody can commit is refused here, not three turns later.

    The host takes the committable patch by watching the ``plan_apply`` this
    turn made -- never from the model, because a model-written basis is a
    forged one -- and a PostToolUse hook records it. So the presence of that
    record is exactly the question "was this candidate applied?", asked of the
    filesystem rather than of the model.

    Two instructions were tried first and did not move the rate: the turn
    preamble (1a4cedb) and this tool's own description (051f4eb). Measured over
    11 real candidate turns, 7 applied and 4 short-circuited straight to this
    call, two of them without even reading the day. `submit_planning_result` is
    callable at any moment, so ending early always satisfies the letter of "end
    the turn by calling it once".

    Refusing here is the profile's `toolFilter` argument in a different place:
    calling the wrong one stops being instructed against and becomes
    impossible. And it lands *during* the turn, while the planner still has
    steps left to apply and resubmit, rather than failing the whole turn after
    it has ended.

    A blocker carries no artifact and a skeleton is never committed, so neither
    needs a patch.
    """

    if target_artifact != ArtifactKind.VALIDATED_CANDIDATE.value:
        return
    if artifact is None:
        return
    configured = os.environ.get(CANDIDATE_OUTPUT_FILE_ENV, "").strip()
    if configured and Path(configured).exists():
        return
    raise PlanningResultRefused(
        "this candidate has no patch behind it, so nothing could be committed "
        "from it. Call `plan_apply` on tmbx to put the day in, then submit "
        "again -- the host takes the patch from that call and cannot accept "
        "one written into `artifact`."
    )


def _refuse_missing_required_block(*, target_artifact: str, artifact: Any) -> None:
    """A candidate that lacks a block of a required kind is refused here, by name.

    The host publishes the slugs the day's rules require; presence is read from
    the captured `plan_apply` -- the ops that added or updated a block with that
    slug, and the rows tmbx resolved (a block already on the day carries its
    slug there). Never from the artifact: a model-written claim of presence is
    the forged basis the captured-patch guard exists to refuse.

    Fails open when the host publishes nothing, like the open-requirements
    guard: the kernel repeats this check when it accepts the draft, so the only
    loss is the early correction.

    It also stays quiet on a capture that says nothing about the candidate --
    missing, empty, unparseable, or carrying neither a patch nor rows. Absence
    of evidence is not a missing block, and refusing there would name the wrong
    cause: it sends the planner to add a block the plan may already have, while
    the real failure (nothing was applied, or the capture broke) goes unsaid.
    `CandidateNotApplied` and the unapplied guard above own that failure and
    name it. Once a real capture is in hand, this never fails open.
    """
    if target_artifact != ArtifactKind.VALIDATED_CANDIDATE.value or artifact is None:
        return
    configured = os.environ.get(REQUIRED_BLOCKS_FILE_ENV, "").strip()
    if not configured:
        return
    try:
        published = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    required = {str(s) for s in published if isinstance(s, str) and s} if isinstance(published, list) else set()
    if not required:
        return
    captured = os.environ.get(CANDIDATE_OUTPUT_FILE_ENV, "").strip()
    if not captured:
        return
    try:
        payload = json.loads(Path(captured).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("patch") is None and payload.get("rows") is None:
        return
    missing = required - slugs_on_candidate(payload)
    if not missing:
        return
    raise PlanningResultRefused(
        "[required_block_missing] this candidate has no block of a kind the "
        "day requires: "
        + ", ".join(sorted(missing))
        + ". Add one with `plan_apply` and `slug` set to exactly that word, "
        "then submit again. The requirement is candidate.required_blocks; "
        "record its time as your assumption."
    )


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
    continuation: dict[str, Any] | None = None,
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
    if artifact is None and not blockers and continuation is None:
        raise PlanningResultRefused(
            "this submission carries neither an artifact, a blocker, nor a "
            "continuation, so the turn would end with nothing for the user to "
            "review and nothing to resume from."
        )
    if artifact is not None and target_artifact == ArtifactKind.SKELETON.value:
        # The one payload whose shape the card depends on. A skeleton that
        # arrives without `markdown` is stored, approved, and drawn as an empty
        # day (#267); refusing it here costs the planner one retry in the same
        # turn, with the field names in hand.
        try:
            SkeletonPayload.model_validate(artifact)
        except ValidationError as exc:
            raise PlanningResultRefused(
                "a skeleton payload is {\"markdown\": <the day as loose "
                "markdown>, \"reasoning\": <why it is shaped that way>} and "
                f"nothing else; this one does not match ({_shape_codes(exc)})."
            ) from exc

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
                "continuation": continuation,
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
        SkeletonPayload,
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

    The path is safe to repeat because every segment is either an index, a
    field name this system declared, or -- for `extra_forbidden` -- the key
    the model just invented. That last case used to be replaced rather than
    echoed, on the theory that Pydantic's own message quotes model-authored
    text; but the invented key is exactly the name a planner needs back to
    stop inventing it, this refusal returns to no one but the model that just
    wrote the key, and #267's skeleton went unrefused for want of it. Any
    other unrecognised segment (a value, not a key this tool ever declared)
    is still masked.
    """

    codes = set()
    for error in exc.errors():
        extra_key = error["type"] == "extra_forbidden"
        parts = [
            str(segment)
            if isinstance(segment, int) or segment in _KNOWN_FIELDS or extra_key
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
    "OPEN_REQUIREMENTS_FILE_ENV",
    "PLANNING_RESULT_FILE_ENV",
    "REQUIRED_BLOCKS_FILE_ENV",
    "PlanningResultRefused",
    "mcp",
    "submit_planning_result",
]
