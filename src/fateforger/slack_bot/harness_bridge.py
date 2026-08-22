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

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

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
    text: str
    profile: str
    #: Wall-clock seconds per phase. Recorded because the shape of a slow turn
    #: is not guessable from the outside: on this stack most of a cold turn is
    #: process boot, not the model, and optimising the wrong one is free to do
    #: and useless.
    timings: dict[str, float] | None = None


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


def compose_task(text: str, *, history: list[tuple[str, str]] | None = None) -> str:
    """Build the task string for one turn.

    ``history`` is ``(speaker, message)`` oldest-first — the thread so far.
    Replayed verbatim rather than summarised: a summary is a judgement about
    what mattered, and that judgement belongs to the model reading it, not to
    this adapter.
    """
    if not history:
        return text
    prior = "\n".join(f"{speaker}: {message}" for speaker, message in history)
    return f"Earlier in this thread:\n{prior}\n\nHugo now says:\n{text}"


def ask(
    text: str,
    *,
    history: list[tuple[str, str]] | None = None,
    profile: str = _PROFILE,
    env: dict[str, str] | None = None,
) -> HarnessReply:
    """Run one Slack turn through the harness and return what it said."""
    child_env = {
        **os.environ,
        **_repo_env(),
        "DSH_HOME": str(_DSH_HOME),
        **(env or {}),
    }
    try:
        done = subprocess.run(
            _cli_args(compose_task(text, history=history), profile),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            cwd=str(_REPO),
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"harness exceeded {_TIMEOUT_S:.0f}s") from exc

    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines()[-5:]
        raise HarnessError(
            f"harness exited {done.returncode}: " + " / ".join(tail)
        )

    answer = (done.stdout or "").strip()
    if not answer:
        raise HarnessError("harness produced no output")
    return HarnessReply(text=answer, profile=profile)


#: Markers the harness and the MCP servers emit on their way to an answer.
#: These are *our own* servers' log lines and the harness's own protocol
#: chatter — system-minted strings, not user content — so recognising them is
#: identifier matching, not a judgement about what anyone meant.
_BOOTED = "memory-allowlist:"
_TOOLS_READY = "ListToolsRequest"
_TOOL_CALL = "CallToolRequest"

#: Log lines our own MCP servers and their libraries write to the same stream
#: the answer arrives on. Fixed strings emitted by code in this repo or its
#: dependencies — never anything a person wrote.
_CHATTER = (
    "server.py:",
    "Processing request of type",
    "warnings.warn",
    "IncompleteFieldDefinitionWarning",
    "/site-packages/",
)


def _is_runtime_chatter(line: str) -> bool:
    """True for log output rather than answer text.

    stderr is merged into stdout so progress stays observable in order, which
    means the answer and the servers' logging share one stream and have to be
    told apart here.
    """
    return any(marker in line for marker in _CHATTER)


def ask_streaming(
    text: str,
    *,
    on_event: Callable[[str], None],
    history: list[tuple[str, str]] | None = None,
    profile: str = _PROFILE,
) -> HarnessReply:
    """Run a turn, reporting progress as it happens.

    The CLI does not token-stream — the answer arrives in one block at the end
    — so there is no partial text to forward. What *is* observable is the shape
    of the run, and on a cold start that is most of the wall clock: roughly two
    thirds of a turn is the Node runtime and two Python MCP servers booting
    before a single token is generated.

    So ``on_event`` reports phases, not prose. It turns a silent minute into
    something a person can watch, which is the actual complaint.
    """
    proc = subprocess.Popen(
        _cli_args(compose_task(text, history=history), profile),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(_REPO),
        env={**os.environ, **_repo_env(), "DSH_HOME": str(_DSH_HOME)},
    )

    started = time.monotonic()
    timings: dict[str, float] = {}
    answer: list[str] = []
    tool_calls = 0
    announced: set[str] = set()

    def mark(phase: str) -> None:
        timings.setdefault(phase, round(time.monotonic() - started, 1))

    def announce(key: str, message: str) -> None:
        if key not in announced:
            announced.add(key)
            on_event(message)

    assert proc.stdout is not None
    for line in proc.stdout:
        if _BOOTED in line:
            mark("boot")
            announce("boot", "servers up")
        elif _TOOLS_READY in line:
            mark("tools_ready")
            announce("tools", "tools ready")
        elif _TOOL_CALL in line:
            mark("first_tool_call")
            tool_calls += 1
            on_event(f"working — {tool_calls} tool call{'s' if tool_calls > 1 else ''}")
        elif _is_runtime_chatter(line):
            continue
        else:
            answer.append(line)

    if proc.wait() != 0:
        raise HarnessError(f"harness exited {proc.returncode}")
    mark("answer")
    timings["tool_calls"] = tool_calls
    joined = "".join(answer).strip()
    if not joined:
        raise HarnessError("harness produced no output")
    return HarnessReply(text=joined, profile=profile, timings=timings)
