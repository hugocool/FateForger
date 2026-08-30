"""Slack ↔ DeepSeek Harness.

The harness owns the loop. This module owns transport and nothing else: no
planning, no tool choice, no retry policy. Anything resembling a judgement
belongs in the profile's system prompt or an MCP server, never here.

**Why a subprocess and not the SDK.** ``deepseek-harness/python/sdk`` exposes
exactly the right shape — ``DeepSeekHarness.start_session(thread_ts)`` — but its
runtime carrier, ``deepseek-harness-runtime-bin``, is still a name-reservation
placeholder on PyPI. Both MCP servers boot under the monorepo's stand-in entry
and then ``initialize`` never returns. So this drives the CLI, which is the path
every result in the two mount reports came from. ``ask()`` is the seam: when the
carrier ships, its body changes and no caller does.

**Why each turn is a fresh run.** The headless profile answers one task and
exits; ``--resume`` belongs to the TUI. That sounds like a limitation and mostly
is not, because durable state does not live in the loop — it lives in the
calendar and the constraint store, and the harness re-reads both through
``plan_read`` and ``memory_get_active_constraints`` on every run. What a fresh
run genuinely loses is the *conversation*, so recent thread turns are replayed
as context.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    PlanningBrief,
    PlanningResult,
)

from .dsh_progress_hook import COMMIT_FILE_ENV, PROGRESS_FILE_ENV, ProgressEvent
from .mrkdwn import to_mrkdwn
from .progress_events import (
    ProgressPhase as TimeboxProgressPhase,
)
from .progress_events import (
    ProgressSource,
    TimeboxProgressEvent,
)
from .progress_events import (
    ProgressStatus as TimeboxProgressStatus,
)
from .timebox_candidate import ValidatedTimeboxCandidate
from .validated_timebox_draft import (
    CANDIDATE_OUTPUT_FILE_ENV,
    DRAFT_STATE_FILE_ENV,
    read_validated_candidate,
)

#: Where the PreToolUse commit gate looks for a Slack approval token. Named
#: here because this module is what puts it in the child's environment; the
#: gate hook and the Slack button handler both read the same name.
APPROVAL_FILE_ENV = "FF_DSH_APPROVAL_FILE"

#: Where the planning-result server writes the one typed result a planning turn
#: owes. Provisioned here per turn, because the file *is* the turn: an empty one
#: means nothing has been submitted yet, and a fresh one per run is what makes
#: the server's idempotency check turn-scoped without any state of its own.
#: ``planning_result_mcp`` restates this literal rather than importing it, and
#: ``test_planning_result_mcp`` asserts the two agree.
PLANNING_RESULT_FILE_ENV = "FF_DSH_PLANNING_RESULT_FILE"

logger = logging.getLogger(__name__)

_DSH_HOME = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
_REPO = Path(
    os.environ.get("DSH_REPO", Path.home() / "VScode-projects/deepseek-harness")
)
_PROFILE = os.environ.get("DSH_PROFILE", "tmbx")
_FATEFORGER_ROOT = Path(__file__).resolve().parents[3]

#: A login shell resolves ``node`` to v14 via nvm, and the CLI dies on syntax
#: before printing anything useful. Both mount reports paid ten minutes for
#: this; the absolute path is deliberate.
_NODE = os.environ.get("DSH_NODE", "/opt/homebrew/bin/node")

#: A planning turn reads the calendar, consults the constraint store and may
#: commit a patch. Generous, but not unbounded — an abandoned run that already
#: wrote to the calendar is worse than a slow one.
_TIMEOUT_S = float(os.environ.get("DSH_TIMEOUT_SECONDS", "600"))
_PROGRESS_SESSION_KEY_ENV = "FF_DSH_SESSION_KEY"
_HARNESS_REASONING_ENV = "FF_HARNESS_REASONING"


class HarnessError(RuntimeError):
    """The harness failed to answer. Raised loudly, never swallowed.

    A degraded "I could not reach the planner" reply would be indistinguishable
    from the planner declining to act, which is the silent-wrong-answer shape
    the project bans elsewhere.
    """


class HarnessCancelled(HarnessError):
    """The caller withdrew an owned harness turn and its child was reaped."""


@dataclass(frozen=True)
class HarnessReply:
    #: Already Slack ``mrkdwn``, never the Markdown the harness emitted. The
    #: conversion happens in ``ask`` rather than at either posting site so a
    #: caller added later cannot forget it -- there were two such sites the day
    #: this landed and both had the same bug (#179).
    text: str
    profile: str
    #: Wall-clock seconds per phase. Recorded because the shape of a slow turn
    #: is not guessable from the outside: on this stack most of a cold turn is
    #: process boot, not the model, and optimising the wrong one is free to do
    #: and useless.
    timings: dict[str, float] | None = None
    #: The transaction this turn committed, if it committed one. Present only
    #: for a write that actually landed -- a refused commit carries no id --
    #: so Slack can offer to reverse exactly what happened and nothing else.
    committed_tx_id: str | None = None
    #: True when the commit gate refused this turn for want of an approval.
    #: The model says "press Approve"; this is what makes the button appear.
    needs_approval: bool = False
    #: Exact snapshot+patch validated by tmbx in this run. Private host state;
    #: only its opaque id crosses Slack transport.
    validated_candidate: ValidatedTimeboxCandidate | None = None
    #: The typed artifact this turn produced, when a planning brief demanded
    #: one. Never derived from ``text``: prose is what the turn looked like,
    #: and this is what it did.
    planning_result: PlanningResult | None = None


#: The interpreter the hooks run under. `hooks.json` falls back to
#: `${CLAUDE_PROJECT_DIR}/.venv/bin/python`, which is the project root -- and a
#: git worktree has no `.venv`, so from one every hook failed to start and said
#: nothing. A hook that cannot start looks exactly like a hook with nothing to
#: report, which is how a missing candidate capture surfaced three layers away
#: as a plan that could be approved and never committed.
HOOK_PYTHON_ENV = "FF_HOOK_PYTHON"


def _hook_interpreter() -> str:
    """The interpreter running this process, which by construction can import it.

    The hooks import `fateforger`; so does this module. Anything that can run
    the host can run the host's hooks, and nothing else is guaranteed to.
    """

    return sys.executable


def _repo_env() -> dict[str, str]:
    """Read the repo's own ``.env`` for the child process.

    Called per-invocation and pointed at an explicit path — never at import
    time, and never through ``find_dotenv()``. That combination walks *up* the
    tree and injects whatever it finds into ``os.environ``, which is #178: it
    defeats the ``env_file=None`` guard in ``Settings`` and makes the test suite
    depend on an untracked file in a parent directory.

    Nothing here mutates ``os.environ``. The values go to the child and stop
    there.
    """
    env_file = Path(__file__).resolve().parents[3] / ".env"
    if not env_file.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _cli_args(task: str, profile: str) -> list[str]:
    return [
        _NODE,
        str(_REPO / "apps/cli/lib/bin.js"),
        "--profile",
        profile,
        task,
    ]


def compose_task(
    text: str,
    *,
    history: list[tuple[str, str]] | None = None,
    proposed_timebox: str | None = None,
    proposed_calendar_id: str | None = None,
    proposed_day: str | None = None,
    session_id: str | None = None,
    planning_brief: PlanningBrief | None = None,
) -> str:
    """Build the task string for one turn.

    ``session_id`` is how a thread remembers itself. The harness spawns a fresh
    process per turn, so nothing survives in the model's context -- but the
    memory server holds what this conversation established, keyed by exactly
    this value, and `memory_get_session_constraints` reads it back.

    **It has to be supplied, because the model cannot know it.** The memory
    policy tells the model both memory tools share a session id and that it has
    to be "the *same* value"; nothing told it which. So it invented one per turn
    -- `session_2026_08_24_plan` and `tmbx-session-2026-08-24` are both from
    real traces -- and each turn wrote into a store the next turn could not
    find. Observed 2026-08-24: Hugo said he was on vacation and going to a
    nature reserve at 13:00, answered a follow-up question, and the next turn
    asked him which date he wanted to plan. Same failure shape as the calendar
    id: a required identifier nothing supplied, invented plausibly, wrong
    silently.

    ``history`` is deliberately bounded by the Slack caller to the latest
    three owner turns. That small intent window is paired with the exact
    proposal message named by its approval card; durable constraints still
    live behind ``session_id`` rather than growing an unbounded transcript.

    ``planning_brief`` replaces all of that for an adaptive timeboxing turn.
    The kernel already holds the day, the facts, the artifacts and the
    approvals as typed state, so the brief is the context and the transcript
    is not -- and with it comes the obligation to submit one typed result.
    """
    if planning_brief is not None and (history or proposed_timebox):
        # Documented as "replaces all of that" and, until now, merely appended
        # after it. A caller passing both would have handed the model a
        # transcript *above* the sentence saying the brief is authoritative --
        # the precise contamination Task 7 closed at the planner seam, reopened
        # one layer down. Refusing makes it impossible rather than unlikely.
        raise ValueError(
            "a planning brief is the whole context for its turn; "
            "history and proposed_timebox cannot accompany one"
        )
    parts = []
    if planning_brief is not None:
        # First. An obligation the model reads after a transcript is an
        # obligation it has already begun answering from the transcript.
        parts.append(_planning_obligation(planning_brief))
    if session_id:
        parts.append(
            f"Session id for this conversation: {session_id}\n"
            f"Pass it as `session_id` to memory_get_session_constraints and to "
            f"memory_observe. Read it back before you answer -- it is what this "
            f"thread already established, and you cannot see the earlier turns."
        )
    if history:
        prior = "\n".join(f"{speaker}: {message}" for speaker, message in history)
        parts.append(f"Earlier in this thread:\n{prior}")
    if proposed_timebox:
        target = ""
        if proposed_calendar_id and proposed_day:
            target = (
                f" It belongs to calendar `{proposed_calendar_id}` for day "
                f"`{proposed_day}`; read exactly that target before patching."
            )
        parts.append(
            "Current proposed timebox (the draft baseline Hugo is editing; "
            "its presence here is not evidence that it is already on the calendar)."
            f"{target}\n"
            f"{proposed_timebox}"
        )
    if not parts:
        return text
    if text.strip():
        # A planning turn has no utterance to quote: what Hugo said reached the
        # kernel as typed facts, and the runner's own request is not his. An
        # empty "Hugo now says:" would attribute the host's words to him, which
        # is the invented-provenance defect that cost this system a session id
        # and a calendar id already.
        parts.append(f"Hugo now says:\n{text}")
    return "\n\n".join(parts)


def _planning_obligation(brief: PlanningBrief) -> str:
    """State the host's authority over this turn, then the call that ends it."""

    target = brief.target_artifact.value
    return (
        "This planning turn is host-driven. The brief below is authoritative "
        "for the day, the facts, the prior artifacts and the approvals; do not "
        "re-derive any of them from calendar content or from prose.\n"
        f"{_canonical_brief(brief)}\n"
        f"Produce exactly one `{target}` and end this turn by calling "
        f"`submit_planning_result` once, with target_artifact `{target}`. Your "
        "final message is presentation only: it records nothing, and a turn "
        "that ends without that call has produced nothing."
    )


def _canonical_brief(brief: PlanningBrief) -> str:
    """Serialize the brief the same way every time.

    The brief *is* the prompt. Two identical turns whose context reorders are
    two different prompts: a surprising answer stops being reproducible, and no
    prefix repeats often enough for a provider to cache it. Sorted keys settle
    the mappings; ``allowed_outputs`` is a set, which is the one part Python
    will not settle on its own.
    """

    payload = brief.model_dump(mode="json")
    payload["allowed_outputs"] = sorted(payload["allowed_outputs"])
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


#: How often the progress file is checked while a turn runs. Half a second is
#: below the threshold where a person reads a checklist as frozen, and the cost
#: is one `stat` on a local file.
_POLL_INTERVAL_S = float(os.environ.get("DSH_PROGRESS_POLL_SECONDS", "0.5"))


#: Written by the commit gate, read here. Imported rather than restated so the
#: two halves cannot drift into never matching -- which would present as the
#: button simply never appearing.
from .dsh_commit_gate_hook import NEEDS_APPROVAL as _NEEDS_APPROVAL


def _tail_progress(
    path: Path,
    on_event: Callable[[TimeboxProgressEvent | str], None],
    stop: threading.Event,
    refused: list[bool] | None = None,
    *,
    session_key: str = "unscoped",
) -> int:
    """Report each line the hook appends, until stopped and drained.

    A polling thread rather than the subprocess's own output, because that
    stream stays silent until the answer arrives -- the whole point is to say
    something while the run is still going. It also survives the servers moving
    between stdio and ``streamable-http``, which is what killed the two earlier
    approaches.

    Returns the number of completed harness tool calls. PreToolUse starts and
    agent-authored progress reports are delivered but do not inflate this count.
    """
    offset = 0
    completed_calls = 0
    sequence = 0
    while True:
        finished = stop.is_set()
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            chunk = b""
        # Consume only up to the last newline. Many hook processes append
        # concurrently, so a read can land mid-write; holding the fragment back
        # until its newline arrives is what stops a tool name being reported in
        # two halves.
        consumed = chunk.rfind(b"\n") + 1
        if consumed:
            offset += consumed
            for line in chunk[:consumed].decode("utf-8", "replace").splitlines():
                step = line.strip()
                if not step:
                    continue
                if refused is not None and step == _NEEDS_APPROVAL:
                    # Not a progress step: it is the gate telling Slack to
                    # offer the button. Counting it would report a tool call
                    # that never happened.
                    refused.append(True)
                    continue
                try:
                    direct = TimeboxProgressEvent.from_json(step)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    try:
                        projected = ProgressEvent.from_line(step)
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        logger.warning(
                            "discarded malformed progress line bytes=%d",
                            len(step.encode("utf-8", "replace")),
                        )
                        continue
                    else:
                        sequence += 1
                        forwarded = _transport_progress_event(
                            projected,
                            session_key=session_key,
                            sequence=sequence,
                        )
                else:
                    sequence += 1
                    forwarded = replace(
                        direct,
                        session_key=session_key,
                        sequence=sequence,
                    )
                if _is_completed_tool_event(forwarded):
                    completed_calls += 1
                try:
                    on_event(forwarded)
                except Exception as exc:  # noqa: BLE001 - see below
                    # A failing progress consumer must not take down the turn
                    # it is describing. Same rule ProgressChannel follows, for
                    # the same reason.
                    logger.warning(
                        "progress consumer raised: %s: %s", type(exc).__name__, exc
                    )
        if finished:
            return completed_calls
        stop.wait(_POLL_INTERVAL_S)


def _is_completed_tool_event(event: TimeboxProgressEvent | str) -> bool:
    if isinstance(event, str):
        return not event.startswith("start\t")
    return (
        event.source is ProgressSource.HARNESS_HOOK
        and event.status is not TimeboxProgressStatus.STARTED
    )


def _transport_progress_event(
    event: ProgressEvent,
    *,
    session_key: str,
    sequence: int,
) -> TimeboxProgressEvent:
    """Add host-owned identity to a hook's privacy-bounded domain facts."""

    detail = event.safe_detail
    refusal = detail.get("refusal_reason")
    return TimeboxProgressEvent(
        session_key=session_key,
        sequence=sequence,
        source=ProgressSource.HARNESS_HOOK,
        phase=TimeboxProgressPhase(event.phase.value),
        status=TimeboxProgressStatus(event.status.value),
        attempt=_optional_int(detail.get("attempt")),
        block_count=_optional_int(detail.get("block_count")),
        violation_count=_optional_int(detail.get("violation_count")),
        violation_kinds=tuple(
            kind for kind in detail.get("violation_kinds", ()) if isinstance(kind, str)
        ),
        overspecified_count=_optional_int(detail.get("overspecified_count")),
        refusal_code=refusal if isinstance(refusal, str) else None,
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


#: The model a planning turn runs on, overriding the profile's fast default.
#:
#: The loop and the planner want different things and share a process. The loop
#: is latency-bound and mostly emits tool calls; timeboxing and patching are
#: correctness-bound and tolerate a slower call, because a wrong patch costs a
#: retry and the expected wait is one attempt divided by the success rate.
#:
#: Sail Research via `:nitro`, which is also one of the hosts that enforces
#: `structured_outputs` -- a per-host property, and a patch IS structured
#: output, so an unenforcing host would accept a malformed one in silence.
PLANNING_MODEL = os.environ.get(
    "FF_PLANNING_MODEL", "deepseek/deepseek-v4-pro-0813:nitro"
)


def ask(
    text: str,
    *,
    history: list[tuple[str, str]] | None = None,
    proposed_timebox: str | None = None,
    proposed_calendar_id: str | None = None,
    proposed_day: str | None = None,
    session_id: str | None = None,
    planning_brief: PlanningBrief | None = None,
    model: str | None = None,
    profile: str = _PROFILE,
    env: dict[str, str] | None = None,
    on_event: Callable[[TimeboxProgressEvent | str], None] | None = None,
    approval_file: str | Path | None = None,
    cancel_event: threading.Event | None = None,
) -> HarnessReply:
    """Run one Slack turn through the harness and return what it said.

    With ``on_event``, each completed tool call is reported as it happens: the
    profile's ``PostToolUse`` hook appends the tool's name to a per-run file and
    a polling thread forwards it. Without it, nothing is set and the hook is a
    no-op, so a headless run costs nothing for a feature it is not using.

    ``planning_brief`` turns this into an adaptive timeboxing turn. The brief
    goes into the task, a result file is provisioned for the planning-result
    server, and the reply carries whatever typed artifact the planner
    submitted. A brief with no result is a failed turn and raises: prose cannot
    satisfy the contract, because the kernel has to hand the user something
    reviewable and prose is not it. Without a brief nothing changes, which is
    what every non-timeboxing ``/dsh`` call depends on.

    ``approval_file`` is where the commit gate looks for a Slack approval
    token. **The caller owns the path, not this module.** A path minted here
    would be invisible to the Slack button handler that has to write into it,
    so the two would never meet -- and the gate would deny every commit while
    looking correctly configured. Absence is not neutral: with no path set the
    gate denies, which is the safe direction and the intended default.
    """
    child_env = {
        **os.environ,
        **_repo_env(),
        "DSH_HOME": str(_DSH_HOME),
        # The profile is durable under ~/.dsh but the code may be running from
        # an issue worktree. Every profile path that executes or reads repo
        # artifacts resolves through this host-owned root.
        "FF_FATEFORGER_ROOT": str(_FATEFORGER_ROOT),
        # Read by the profile's `agent-default-model` entry. Absent leaves the
        # profile's own fast default in force, which is what a headless or
        # non-planning call should get.
        **({"FF_HARNESS_MODEL": model} if model else {}),
        **({_HARNESS_REASONING_ENV: "low"} if model else {}),
        **(env or {}),
    }
    if approval_file is not None:
        # Absolute, because the hook runs with the session workspace as its cwd
        # and a relative path would resolve somewhere neither side agreed on --
        # which reads as "no approval" and denies a commit the user did grant.
        child_env[APPROVAL_FILE_ENV] = str(Path(approval_file).resolve())

    started = time.monotonic()
    steps = 0
    last_tx_id: str | None = None
    candidate: ValidatedTimeboxCandidate | None = None
    planning_result: PlanningResult | None = None

    with tempfile.TemporaryDirectory(prefix="dsh-progress-") as workspace:
        progress = Path(workspace) / "steps"
        progress.touch()
        commits = Path(workspace) / "commits"
        commits.touch()
        child_env[COMMIT_FILE_ENV] = str(commits)
        # The hooks inherit this and `hooks.json` prefers it over the project
        # root's `.venv`, which does not exist in a worktree.
        child_env[HOOK_PYTHON_ENV] = _hook_interpreter()
        validated_draft = Path(workspace) / "validated-draft.json"
        child_env[DRAFT_STATE_FILE_ENV] = str(validated_draft)
        candidate_output = Path(workspace) / "candidate.json"
        child_env[CANDIDATE_OUTPUT_FILE_ENV] = str(candidate_output)
        planning_result_file: Path | None = None
        if planning_brief is not None:
            # Created empty rather than left absent: the server reads the file
            # to learn whether this turn has already submitted, and "missing"
            # and "nothing yet" would otherwise be the same thing.
            planning_result_file = Path(workspace) / "planning-result.json"
            planning_result_file.touch()
            child_env[PLANNING_RESULT_FILE_ENV] = str(planning_result_file)
        stop = threading.Event()
        tail: threading.Thread | None = None
        collected: list[int] = []
        # One entry per gate refusal seen this turn; presence is the whole signal.
        refused: list[bool] = []

        if on_event is not None:
            child_env[PROGRESS_FILE_ENV] = str(progress)
            child_env[_PROGRESS_SESSION_KEY_ENV] = session_id or "unscoped"
            tail = threading.Thread(
                target=lambda: collected.append(
                    _tail_progress(
                        progress,
                        on_event,
                        stop,
                        refused,
                        session_key=session_id or "unscoped",
                    )
                ),
                daemon=True,
            )
            tail.start()

        try:
            args = _cli_args(
                compose_task(
                    text,
                    history=history,
                    proposed_timebox=proposed_timebox,
                    proposed_calendar_id=proposed_calendar_id,
                    proposed_day=proposed_day,
                    session_id=session_id,
                    planning_brief=planning_brief,
                ),
                profile,
            )
            if cancel_event is None:
                done = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_TIMEOUT_S,
                    cwd=str(_REPO),
                    env=child_env,
                )
            else:
                done = _run_cancellable(
                    args,
                    cwd=str(_REPO),
                    env=child_env,
                    cancel_event=cancel_event,
                    timeout_s=_TIMEOUT_S,
                )
        except subprocess.TimeoutExpired as exc:
            raise HarnessError(f"harness exceeded {_TIMEOUT_S:.0f}s") from exc
        finally:
            # Set before joining so the thread makes one final pass and drains
            # whatever landed between its last poll and the process exiting.
            stop.set()
            if tail is not None:
                tail.join(timeout=_POLL_INTERVAL_S * 4)
                steps = collected[0] if collected else 0
            # Read before the workspace is removed. The last id wins: a turn
            # may commit more than once, and the most recent write is the one
            # an Undo control offered now would reverse.
            try:
                recorded = [
                    line.strip()
                    for line in commits.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except OSError:
                recorded = []
            last_tx_id = recorded[-1] if recorded else None
            candidate = read_validated_candidate(candidate_output)
            planning_result = _read_planning_result(planning_result_file)

    if done.returncode != 0:
        # Both streams, because a failing turn does not reliably use stderr.
        # Two intermittent failures on 2026-08-24 -- one PI_AI_ERROR, one bare
        # exit 1 -- produced an empty stderr, so this raised "harness exited 1:"
        # with nothing after the colon. An error that names no cause is the
        # same silent-wrong-answer shape as no error at all: it tells the
        # reader something broke and denies them any way to find out what.
        detail = " / ".join((done.stderr or "").strip().splitlines()[-5:])
        if not detail:
            detail = " / ".join((done.stdout or "").strip().splitlines()[-5:])
        if not detail:
            detail = "no output on either stream"
        raise HarnessError(f"harness exited {done.returncode}: {detail}")

    if planning_brief is not None and planning_result is None:
        # The regression this seam closes. A planner can exit zero having
        # described a plan it never submitted, and every earlier version of
        # this call would have returned that prose as the turn's work.
        raise HarnessError("planner exited without the required typed planning result")

    # A candidate offered for approval is displayed from tmbx's own validated
    # render, never from model prose that could describe a different draft.
    answer = (
        candidate.rendered.strip()
        if candidate is not None and candidate.rendered.strip()
        else (done.stdout or "").strip()
    )
    if not answer and planning_result is None:
        # Silence is only a failure when nothing else stands in for the answer.
        # A planning turn renders from its typed artifact, so an empty stdout
        # there is a quiet planner, not an empty turn.
        raise HarnessError("harness produced no output")
    # Emptiness is judged on what the harness said, not on what survives
    # conversion: a reply that renders to nothing is still a reply, and
    # reporting it as "no output" would blame the wrong side.
    return HarnessReply(
        text=to_mrkdwn(answer),
        profile=profile,
        timings={
            "elapsed_s": round(time.monotonic() - started, 1),
            "tool_calls": steps,
        },
        needs_approval=bool(refused),
        # Computed a few lines up and, until now, dropped on the floor: the
        # hook recorded it, the bridge read it, and nothing carried it out, so
        # `handlers` offered Undo only when `reply.committed_tx_id` was set and
        # it never was. Third wire this week that was built at both ends and
        # joined nowhere.
        committed_tx_id=last_tx_id,
        validated_candidate=candidate,
        planning_result=planning_result,
    )


def _read_planning_result(source: Path | None) -> PlanningResult | None:
    """Load the turn's typed result, or nothing at all.

    Only ``planning_result_mcp`` writes this file, and only after validating it
    and renaming it into place, so anything unreadable here means the
    submission never happened. There is no partial credit: an unparseable
    document and an empty one are the same failed turn to the caller, and
    reporting them differently would suggest a result it could act on.
    """

    if source is None:
        return None
    try:
        document = source.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not document:
        return None
    try:
        return PlanningResult.model_validate_json(document)
    except ValidationError as exc:
        logger.warning(
            "planning result did not validate error_type=%s",
            type(exc).__name__,
        )
        return None


def _run_cancellable(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    cancel_event: threading.Event,
    timeout_s: float,
) -> subprocess.CompletedProcess[str]:
    """Run one owned child, terminating it when the caller withdraws the turn."""

    if cancel_event.is_set():
        raise HarnessCancelled("harness turn cancelled before launch")

    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_s
    while True:
        if cancel_event.is_set():
            _terminate_process(process)
            raise HarnessCancelled("harness turn cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process)
            raise subprocess.TimeoutExpired(args, timeout_s)
        try:
            stdout, stderr = process.communicate(timeout=min(0.1, remaining))
        except subprocess.TimeoutExpired:
            continue
        return subprocess.CompletedProcess(
            args=args,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate, then force-kill, and always reap an owned child."""

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (AttributeError, OSError):
            process.terminate()
    try:
        process.communicate(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            process.kill()
        process.communicate()
