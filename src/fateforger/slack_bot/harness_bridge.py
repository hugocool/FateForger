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

import logging
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .dsh_progress_hook import COMMIT_FILE_ENV, PROGRESS_FILE_ENV
from .mrkdwn import to_mrkdwn

#: Where the PreToolUse commit gate looks for a Slack approval token. Named
#: here because this module is what puts it in the child's environment; the
#: gate hook and the Slack button handler both read the same name.
APPROVAL_FILE_ENV = "FF_DSH_APPROVAL_FILE"

logger = logging.getLogger(__name__)

_DSH_HOME = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh"))
_REPO = Path(os.environ.get("DSH_REPO", Path.home() / "VScode-projects/deepseek-harness"))
_PROFILE = os.environ.get("DSH_PROFILE", "tmbx")

#: A login shell resolves ``node`` to v14 via nvm, and the CLI dies on syntax
#: before printing anything useful. Both mount reports paid ten minutes for
#: this; the absolute path is deliberate.
_NODE = os.environ.get("DSH_NODE", "/opt/homebrew/bin/node")

#: A planning turn reads the calendar, consults the constraint store and may
#: commit a patch. Generous, but not unbounded — an abandoned run that already
#: wrote to the calendar is worse than a slow one.
_TIMEOUT_S = float(os.environ.get("DSH_TIMEOUT_SECONDS", "600"))


class HarnessError(RuntimeError):
    """The harness failed to answer. Raised loudly, never swallowed.

    A degraded "I could not reach the planner" reply would be indistinguishable
    from the planner declining to act, which is the silent-wrong-answer shape
    the project bans elsewhere.
    """


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
    session_id: str | None = None,
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

    ``history`` remains for callers that genuinely have a transcript, but the
    Slack path deliberately does not use it. Replaying the thread grows the
    prompt every turn and rebuilds it from the front, so the only stable prefix
    is the system prompt and every turn pays full price. Carrying a session id
    instead keeps the prefix identical turn to turn -- which is what a provider
    cache can actually reuse -- and moves the state into the store built to
    hold it, where it is structured rather than a transcript to re-read.
    """
    parts = []
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
    if not parts:
        return text
    parts.append(f"Hugo now says:\n{text}")
    return "\n\n".join(parts)


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
    on_event: Callable[[str], None],
    stop: threading.Event,
    refused: list[bool] | None = None,
) -> int:
    """Report each line the hook appends, until stopped and drained.

    A polling thread rather than the subprocess's own output, because that
    stream stays silent until the answer arrives -- the whole point is to say
    something while the run is still going. It also survives the servers moving
    between stdio and ``streamable-http``, which is what killed the two earlier
    approaches.

    Returns the number of steps seen, which is the run's completed tool calls.
    """
    offset = 0
    seen = 0
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
                seen += 1
                try:
                    on_event(step)
                except Exception as exc:  # noqa: BLE001 - see below
                    # A failing progress consumer must not take down the turn
                    # it is describing. Same rule ProgressChannel follows, for
                    # the same reason.
                    logger.warning("progress consumer raised: %s: %s", type(exc).__name__, exc)
        if finished:
            return seen
        stop.wait(_POLL_INTERVAL_S)


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
    session_id: str | None = None,
    model: str | None = None,
    profile: str = _PROFILE,
    env: dict[str, str] | None = None,
    on_event: Callable[[str], None] | None = None,
    approval_file: str | Path | None = None,
) -> HarnessReply:
    """Run one Slack turn through the harness and return what it said.

    With ``on_event``, each completed tool call is reported as it happens: the
    profile's ``PostToolUse`` hook appends the tool's name to a per-run file and
    a polling thread forwards it. Without it, nothing is set and the hook is a
    no-op, so a headless run costs nothing for a feature it is not using.

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
        # Read by the profile's `agent-default-model` entry. Absent leaves the
        # profile's own fast default in force, which is what a headless or
        # non-planning call should get.
        **({"FF_HARNESS_MODEL": model} if model else {}),
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

    with tempfile.TemporaryDirectory(prefix="dsh-progress-") as workspace:
        progress = Path(workspace) / "steps"
        progress.touch()
        commits = Path(workspace) / "commits"
        commits.touch()
        child_env[COMMIT_FILE_ENV] = str(commits)
        stop = threading.Event()
        tail: threading.Thread | None = None
        collected: list[int] = []
        # One entry per gate refusal seen this turn; presence is the whole signal.
        refused: list[bool] = []

        if on_event is not None:
            child_env[PROGRESS_FILE_ENV] = str(progress)
            tail = threading.Thread(
                target=lambda: collected.append(_tail_progress(progress, on_event, stop, refused)),
                daemon=True,
            )
            tail.start()

        try:
            done = subprocess.run(
                _cli_args(compose_task(text, history=history, session_id=session_id), profile),
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_S,
                cwd=str(_REPO),
                env=child_env,
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

    answer = (done.stdout or "").strip()
    if not answer:
        raise HarnessError("harness produced no output")
    # Emptiness is judged on what the harness said, not on what survives
    # conversion: a reply that renders to nothing is still a reply, and
    # reporting it as "no output" would blame the wrong side.
    return HarnessReply(
        text=to_mrkdwn(answer),
        profile=profile,
        timings={"elapsed_s": round(time.monotonic() - started, 1), "tool_calls": steps},
        needs_approval=bool(refused),
        # Computed a few lines up and, until now, dropped on the floor: the
        # hook recorded it, the bridge read it, and nothing carried it out, so
        # `handlers` offered Undo only when `reply.committed_tx_id` was set and
        # it never was. Third wire this week that was built at both ends and
        # joined nowhere.
        committed_tx_id=last_tx_id,
    )
