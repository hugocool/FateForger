#!/usr/bin/env python3
"""Supervise the three-process demo stack: start, stop, restart, status.

The problem this solves is not "run three commands". It is that a warm server
keeps answering after the code beneath it has moved, and nothing says so. The
tmbx and memory servers once served six-hour-old code while every symptom
looked like a logic bug in the files on disk -- files that were never the ones
being executed.

So the state file records, per process, a SHA-256 over the *contents* of the
Python sources that process imports, taken at the moment it was started.
``status`` recomputes that hash and compares. Git HEAD is recorded alongside
it but is only ever informational: this repository is worked on with a dirty
tree, so an unchanged HEAD says nothing about whether the files moved. The
content hash is the signal; the SHA is the human-readable anchor next to it.

``stop`` reads PIDs from the same file, so nobody has to hunt for them, and it
signals the process *group*. It refuses to signal a PID whose start time no
longer matches the recorded one: a recycled PID belongs to somebody else, and
killing a stranger is worse than leaving a stale record behind.

Ports are proved by connecting to them. "The process is alive" is a different
and weaker claim -- a second copy that lost the bind race is alive, idle and
indistinguishable from the real server by PID alone, which is how two tmbx
processes came to exist with only one of them serving. Anyone extending this:
``/dev/tcp`` is a bash builtin, it silently does nothing under zsh, and that
is why the check below opens a socket instead.

Usage:

    ./.venv/bin/python scripts/demo.py status [--json]
    ./.venv/bin/python scripts/demo.py start [--reclaim]
    ./.venv/bin/python scripts/demo.py stop
    ./.venv/bin/python scripts/demo.py restart [--reclaim]

``status`` exits 0 only when every service is running, serving, and running
the code currently on disk, so it works as a gate inside a script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The Slack bot has no console entry point; this is the same one-liner the
# stack has always been started with, kept in one place so the state file
# records exactly what was run.
SLACK_BOT_CODE = (
    "import asyncio,logging; "
    "logging.basicConfig(level=logging.INFO); "
    "from fateforger.slack_bot.bot import start; "
    "asyncio.run(start())"
)

# Owned by another session (`~/.dsh/profiles/tmbx/`). Read, fingerprinted,
# never written. DEMO_MEMORY_SERVER overrides it, which is also how the tests
# point at a file they control.
DEFAULT_MEMORY_SERVER = Path.home() / ".dsh" / "profiles" / "tmbx" / "memory-readonly-server.py"


class Problem(Enum):
    """What is wrong with one service. Empty tuple of these means healthy."""

    NOT_STARTED = "not-started"
    GONE = "gone"
    RECYCLED = "recycled"
    PORT_SILENT = "port-silent"
    PORT_FOREIGN = "port-foreign"
    STALE_CODE = "stale-code"


@dataclass(frozen=True)
class ServiceSpec:
    """How to start one process, and which sources decide whether it is stale."""

    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    port: int | None
    source_roots: tuple[Path, ...]


@dataclass(frozen=True)
class Record:
    """What was true at the moment the process was started."""

    name: str
    pid: int
    pgid: int
    started_at: str
    ps_start: str | None
    git_sha: str | None
    fingerprint: str
    port: int | None
    log_path: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class Observed:
    """What is true now, for one recorded service."""

    pid_alive: bool
    ps_start: str | None
    port_accepting: bool
    listener_pids: frozenset[int] | None
    fingerprint: str
    git_sha: str | None


def memory_server_path() -> Path:
    override = os.environ.get("DEMO_MEMORY_SERVER")
    return Path(override) if override else DEFAULT_MEMORY_SERVER


def port_offset() -> int:
    """Shift every port, so a second stack can run beside the live one.

    Set ``DEMO_PORT_OFFSET=10000`` and the servers bind 18010/18011 with their
    own state file -- which is how this supervisor gets smoke-tested without
    taking down the servers answering somebody's Slack thread. One offset moves
    the declared port and the environment variable together, by construction,
    so the two can never disagree; two separate knobs could, and a `status`
    polling a port its server was never asked to open reports a healthy process
    as dead.
    """
    raw = os.environ.get("DEMO_PORT_OFFSET", "0")
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"DEMO_PORT_OFFSET must be an integer; got {raw!r}")


def service_specs(repo_root: Path) -> tuple[ServiceSpec, ...]:
    """The three processes, in start order.

    ``source_roots`` is per-service on purpose: editing the Slack bot does not
    make the memory server stale, and reporting it as stale would train the
    reader to ignore the one column that matters. ``fateforger`` imports
    ``tmbx.journal``, so the bot carries both roots; ``memory`` carries the
    out-of-repo launcher another session owns, which is exactly the file most
    likely to move without anyone here noticing.
    """
    venv = repo_root / ".venv" / "bin"
    src = repo_root / "src"
    mem_server = memory_server_path()
    offset = port_offset()
    tmbx_port = 8011 + offset
    memory_port = 8010 + offset
    return (
        ServiceSpec(
            name="tmbx",
            argv=(str(venv / "tmbx-mcp"),),
            env={
                "TMBX_MCP_TRANSPORT": "streamable-http",
                "TMBX_MCP_PORT": str(tmbx_port),
                "TMBX_CALENDAR_BACKEND": "google",
                "MCP_CALENDAR_SERVER_URL": "http://localhost:3000",
                "PYTHONPATH": str(src),
            },
            port=tmbx_port,
            source_roots=(src / "tmbx",),
        ),
        ServiceSpec(
            name="memory",
            argv=(str(venv / "python"), str(mem_server)),
            env={
                "MEMORY_JUDGE": "openrouter",
                "MEMORY_MCP_TRANSPORT": "streamable-http",
                "MEMORY_MCP_PORT": str(memory_port),
                "MEMORY_DB_PATH": str(repo_root / "data" / "memory.db"),
                "PYTHONPATH": str(src),
            },
            port=memory_port,
            source_roots=(src / "memory", mem_server),
        ),
        ServiceSpec(
            name="slack-bot",
            argv=(str(venv / "python"), "-c", SLACK_BOT_CODE),
            env={"PYTHONPATH": str(src)},
            port=None,
            source_roots=(src / "fateforger", src / "tmbx"),
        ),
    )


# ---------------------------------------------------------------------------
# Code identity
# ---------------------------------------------------------------------------


def python_files(root: Path) -> list[Path]:
    """Every ``.py`` under ``root``, or ``root`` itself when it is a file.

    A root that does not exist yields nothing rather than raising: a deleted
    source tree is drift, and drift is what the caller asked about. Raising
    here would turn `status` into a crash at the moment it is most needed.
    """
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def fingerprint_sources(roots: Iterable[Path]) -> str:
    """SHA-256 over the paths and contents of the given Python sources.

    Contents, not mtimes: a checkout that restores a file byte-for-byte has
    not changed the running code, and a touch that changes nothing should not
    read as a redeploy. Paths are hashed too, so a rename is drift.
    """
    digest = hashlib.sha256()
    for root in roots:
        digest.update(str(root).encode("utf-8"))
        digest.update(b"\x00")
        for path in python_files(root):
            digest.update(str(path).encode("utf-8"))
            digest.update(b"\x00")
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
            digest.update(b"\x00")
    return digest.hexdigest()


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), text=True, capture_output=True)


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def git_head(repo_root: Path, *, runner: Runner = _run) -> str | None:
    proc = runner(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


# ---------------------------------------------------------------------------
# Process and port observation
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive and owned by somebody else. Still alive.
        return True
    return True


def process_start_time(pid: int, *, runner: Runner = _run) -> str | None:
    """The kernel's start time for ``pid``, as ``ps`` prints it.

    This is the recycled-PID guard. Asked for on its own so nothing has to
    split a ``ps`` line into fields.
    """
    proc = runner(["ps", "-p", str(pid), "-o", "lstart="])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def process_command(pid: int, *, runner: Runner = _run) -> str | None:
    proc = runner(["ps", "-p", str(pid), "-o", "command="])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def port_accepting(port: int, *, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """True when something completes a TCP handshake on ``port``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_listener_pids(stdout: str) -> frozenset[int]:
    """PIDs from ``lsof -t`` output: one integer per line."""
    pids: set[int] = set()
    for line in stdout.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            pids.add(int(candidate))
        except ValueError:
            continue
    return frozenset(pids)


def listener_pids(port: int, *, runner: Runner = _run) -> frozenset[int] | None:
    """Who holds ``port``. ``None`` means lsof could not answer, not "nobody"."""
    proc = runner(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    # lsof exits 1 for "no match", which is an answer. Anything else -- most
    # often 127, lsof absent -- is not, and must not read as an empty set.
    if proc.returncode not in (0, 1):
        return None
    return parse_listener_pids(proc.stdout)


# ---------------------------------------------------------------------------
# The judgement
# ---------------------------------------------------------------------------


def classify(record: Record | None, observed: Observed | None) -> tuple[Problem, ...]:
    """Everything wrong with one service, worst-first. Empty means healthy.

    The first three are terminal: if the PID is dead, or belongs to a stranger,
    then nothing downstream is a statement about our process and reporting it
    would be a guess dressed as a finding.
    """
    if record is None or observed is None:
        return (Problem.NOT_STARTED,)
    if not observed.pid_alive:
        return (Problem.GONE,)
    if record.ps_start is not None and observed.ps_start != record.ps_start:
        return (Problem.RECYCLED,)

    problems: list[Problem] = []
    if record.port is not None:
        if not observed.port_accepting:
            problems.append(Problem.PORT_SILENT)
        elif observed.listener_pids is not None and record.pid not in observed.listener_pids:
            # Alive, and something answers -- but not us. This is the shape of
            # a second copy that lost the bind race and has been quietly doing
            # nothing ever since.
            problems.append(Problem.PORT_FOREIGN)
    if observed.fingerprint != record.fingerprint:
        problems.append(Problem.STALE_CODE)
    return tuple(problems)


def observe(record: Record, spec: ServiceSpec, repo_root: Path) -> Observed:
    alive = pid_alive(record.pid)
    return Observed(
        pid_alive=alive,
        ps_start=process_start_time(record.pid) if alive else None,
        port_accepting=port_accepting(record.port) if record.port is not None else False,
        listener_pids=listener_pids(record.port) if record.port is not None else None,
        fingerprint=fingerprint_sources(spec.source_roots),
        git_sha=git_head(repo_root),
    )


def describe(problem: Problem, record: Record, observed: Observed) -> str:
    if problem is Problem.NOT_STARTED:
        return "no record in the state file"
    if problem is Problem.GONE:
        return f"recorded pid {record.pid} is no longer running"
    if problem is Problem.RECYCLED:
        return (
            f"pid {record.pid} is alive but started at {observed.ps_start!r}, "
            f"not {record.ps_start!r} -- the pid was reused, this is not our process"
        )
    if problem is Problem.PORT_SILENT:
        return f"pid {record.pid} is alive but nothing accepts on port {record.port}"
    if problem is Problem.PORT_FOREIGN:
        holders = sorted(observed.listener_pids or ())
        return (
            f"port {record.port} is served by {holders or 'something invisible to lsof'}, "
            f"not by our pid {record.pid} -- this process is running and useless"
        )
    if problem is Problem.STALE_CODE:
        started = (record.git_sha or "?")[:9]
        now = (observed.git_sha or "?")[:9]
        moved = "" if record.git_sha == observed.git_sha else f"; HEAD moved {started} -> {now}"
        return (
            f"sources changed since this process started at {record.started_at}"
            f"{moved} -- it is serving code that is no longer on disk"
        )
    return problem.value


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def state_path(repo_root: Path) -> Path:
    """One state file per port offset.

    A shifted stack that shared the default file would overwrite the PIDs of
    the stack it was deliberately started beside, and `stop` would then be
    unable to name the processes it is supposed to end.
    """
    offset = port_offset()
    name = "state.json" if offset == 0 else f"state+{offset}.json"
    return repo_root / ".demo" / name


def read_state(path: Path) -> dict[str, Record]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{path} is not readable JSON ({exc}). Delete it and re-run `demo start`; "
            f"any processes it described will have to be found with `lsof -nP -iTCP:8010,8011`."
        ) from exc
    out: dict[str, Record] = {}
    for name, entry in (raw.get("services") or {}).items():
        out[name] = Record(
            name=entry["name"],
            pid=int(entry["pid"]),
            pgid=int(entry["pgid"]),
            started_at=entry["started_at"],
            ps_start=entry.get("ps_start"),
            git_sha=entry.get("git_sha"),
            fingerprint=entry["fingerprint"],
            port=entry.get("port"),
            log_path=entry["log_path"],
            argv=tuple(entry.get("argv") or ()),
        )
    return out


def write_state(path: Path, records: Mapping[str, Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "services": {name: asdict(record) for name, record in records.items()},
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Starting and stopping
# ---------------------------------------------------------------------------


def build_env(
    spec: ServiceSpec,
    base: Mapping[str, str],
    dotenv: Mapping[str, str | None],
) -> dict[str, str]:
    """Ambient environment, then ``.env``, then the service's own settings.

    ``.env`` wins over the ambient environment because it is the file the
    operator edits and the one the three processes are documented to share.
    The spec wins over both, because transport and port are this supervisor's
    decision and a stray export must not silently move a server to another
    port where `status` would then report it dead.
    """
    env = dict(base)
    env.update({key: value for key, value in dotenv.items() if value is not None})
    env.update(spec.env)
    return env


def load_dotenv_values(repo_root: Path) -> dict[str, str | None]:
    from dotenv import dotenv_values

    return dict(dotenv_values(repo_root / ".env"))


def log_path_for(repo_root: Path, name: str) -> Path:
    return repo_root / "logs" / "demo" / f"{name}.log"


def tail(path: Path, *, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace")


def stop_record(
    record: Record,
    *,
    killpg: Callable[[int, int], None] = os.killpg,
    alive: Callable[[int], bool] = pid_alive,
    start_time: Callable[[int], str | None] = process_start_time,
    sleep: Callable[[float], None] = time.sleep,
    own_pgid: int | None = None,
    term_polls: int = 100,
    kill_polls: int = 30,
    poll_interval: float = 0.1,
) -> str:
    """Stop one recorded process. Returns an outcome line; never raises.

    Idempotent by construction: an absent PID is a normal outcome, not an
    error, because the whole point is that `stop` can be run by something that
    does not know what is running.
    """
    if not alive(record.pid):
        return "already gone"

    observed_start = start_time(record.pid)
    if record.ps_start is not None and observed_start != record.ps_start:
        return (
            f"refused: pid {record.pid} now belongs to a process started at "
            f"{observed_start!r}, not {record.ps_start!r}. Dropping the record "
            f"without signalling; killing a reused pid kills a stranger."
        )

    pgid = record.pgid
    mine = os.getpgrp() if own_pgid is None else own_pgid
    if pgid <= 0 or pgid == mine:
        return (
            f"refused: recorded pgid {pgid} is not a group this supervisor created "
            f"(our own group is {mine}). Signalling it would take down this process."
        )

    for sig, polls, verb in (
        (signal.SIGTERM, term_polls, "stopped"),
        (signal.SIGKILL, kill_polls, "killed"),
    ):
        try:
            killpg(pgid, sig)
        except ProcessLookupError:
            return "already gone"
        except PermissionError as exc:
            return f"cannot signal pgid {pgid}: {exc}"
        for _ in range(polls):
            if not alive(record.pid):
                return verb
            sleep(poll_interval)
    return f"pid {record.pid} survived SIGKILL"


def start_service(
    spec: ServiceSpec,
    repo_root: Path,
    dotenv: Mapping[str, str | None],
) -> Record:
    log = log_path_for(repo_root, spec.name)
    log.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = fingerprint_sources(spec.source_roots)
    sha = git_head(repo_root)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    handle = log.open("ab")
    try:
        handle.write(
            f"\n===== demo start {stamp} sha={sha} fingerprint={fingerprint[:12]} =====\n".encode()
        )
        handle.flush()
        proc = subprocess.Popen(
            list(spec.argv),
            cwd=str(repo_root),
            env=build_env(spec, os.environ, dotenv),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # Its own session, so the whole tree can be signalled as a group and
            # so a Ctrl-C in this terminal does not reach a server we mean to
            # leave running.
            start_new_session=True,
        )
    finally:
        handle.close()

    return Record(
        name=spec.name,
        pid=proc.pid,
        pgid=proc.pid,  # start_new_session makes the child its own group leader
        started_at=stamp,
        ps_start=process_start_time(proc.pid),
        git_sha=sha,
        fingerprint=fingerprint,
        port=spec.port,
        log_path=str(log),
        argv=tuple(spec.argv),
    )


def wait_for_port(port: int, pid: int, *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return False
        if port_accepting(port, timeout=0.3):
            return True
        time.sleep(0.25)
    return False


def reclaim_port(port: int) -> list[str]:
    """Signal whatever holds ``port``. Only ever called behind ``--reclaim``."""
    notes: list[str] = []
    pids = listener_pids(port)
    if not pids:
        return notes
    for pid in sorted(pids):
        command = process_command(pid) or "?"
        notes.append(f"port {port}: signalling pid {pid} ({command})")
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            except PermissionError as exc:
                notes.append(f"port {port}: cannot signal pid {pid}: {exc}")
                break
            for _ in range(40):
                if not pid_alive(pid):
                    break
                time.sleep(0.1)
            if not pid_alive(pid):
                break
    return notes


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def selected_specs(repo_root: Path, names: Sequence[str]) -> tuple[ServiceSpec, ...]:
    specs = service_specs(repo_root)
    if not names:
        return specs
    known = {spec.name for spec in specs}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise SystemExit(f"unknown service(s) {unknown}; known: {sorted(known)}")
    return tuple(spec for spec in specs if spec.name in set(names))


def _status_rows(repo_root: Path, names: Sequence[str] = ()) -> list[dict[str, object]]:
    records = read_state(state_path(repo_root))
    rows: list[dict[str, object]] = []
    for spec in selected_specs(repo_root, names):
        record = records.get(spec.name)
        if record is None:
            rows.append(
                {
                    "service": spec.name,
                    "pid": None,
                    "port": spec.port,
                    "problems": [Problem.NOT_STARTED.value],
                    "detail": ["no record in the state file"],
                    "git_sha": None,
                }
            )
            continue
        observed = observe(record, spec, repo_root)
        problems = classify(record, observed)
        rows.append(
            {
                "service": spec.name,
                "pid": record.pid,
                "port": record.port,
                "problems": [p.value for p in problems],
                "detail": [describe(p, record, observed) for p in problems],
                "git_sha": record.git_sha,
                "started_at": record.started_at,
                "log": record.log_path,
            }
        )
    return rows


def cmd_status(repo_root: Path, as_json: bool, names: Sequence[str] = ()) -> int:
    rows = _status_rows(repo_root, names)
    if as_json:
        print(json.dumps({"services": rows}, indent=2))
    else:
        width = max((len(str(row["service"])) for row in rows), default=7)
        for row in rows:
            problems = row["problems"]
            assert isinstance(problems, list)
            state = "HEALTHY" if not problems else problems[0].upper()
            pid = row["pid"] if row["pid"] is not None else "-"
            port = row["port"] if row["port"] is not None else "-"
            sha = (row.get("git_sha") or "-")[:9] if row.get("git_sha") else "-"
            print(f"{str(row['service']):<{width}}  pid={pid:<8} port={port:<6} sha={sha:<10} {state}")
            details = row["detail"]
            assert isinstance(details, list)
            for line in details:
                print(f"{'':<{width}}    {line}")
    return 0 if all(not row["problems"] for row in rows) else 1


def cmd_stop(repo_root: Path, names: Sequence[str] = ()) -> int:
    path = state_path(repo_root)
    records = dict(read_state(path))
    wanted = selected_specs(repo_root, names)
    touched = [spec for spec in wanted if spec.name in records]
    if not touched:
        print("nothing recorded as running")
        return 0
    for spec in reversed(touched):
        print(f"{spec.name}: {stop_record(records[spec.name])}")
        records.pop(spec.name, None)
    write_state(path, records)
    return 0


def cmd_start(repo_root: Path, reclaim: bool, names: Sequence[str] = ()) -> int:
    path = state_path(repo_root)
    records = dict(read_state(path))
    dotenv = load_dotenv_values(repo_root)
    failures: list[str] = []

    mem_server = memory_server_path()
    if any(spec.name == "memory" for spec in selected_specs(repo_root, names)) and not mem_server.is_file():
        print(f"memory server launcher not found at {mem_server}", file=sys.stderr)
        return 2

    for spec in selected_specs(repo_root, names):
        existing = records.get(spec.name)
        if existing is not None:
            problems = classify(existing, observe(existing, spec, repo_root))
            if not problems:
                print(f"{spec.name}: already running (pid {existing.pid}), current code")
                continue
            print(f"{spec.name}: replacing existing record -- {problems[0].value}")
            print(f"{spec.name}: {stop_record(existing)}")
            records.pop(spec.name, None)

        if spec.port is not None:
            holders = listener_pids(spec.port)
            if holders:
                if not reclaim:
                    for pid in sorted(holders):
                        print(
                            f"{spec.name}: port {spec.port} already held by pid {pid} "
                            f"({process_command(pid) or '?'})",
                            file=sys.stderr,
                        )
                    print(
                        f"{spec.name}: refusing to start a copy that would lose the bind race. "
                        f"Re-run with --reclaim to signal the holder.",
                        file=sys.stderr,
                    )
                    failures.append(spec.name)
                    continue
                for note in reclaim_port(spec.port):
                    print(note)

        record = start_service(spec, repo_root, dotenv)
        records[spec.name] = record
        write_state(path, records)

        if spec.port is not None:
            if wait_for_port(spec.port, record.pid):
                print(f"{spec.name}: pid {record.pid} listening on {spec.port}")
            else:
                failures.append(spec.name)
                print(f"{spec.name}: did not come up on port {spec.port}", file=sys.stderr)
                print(tail(Path(record.log_path)), file=sys.stderr)
        else:
            # No port to prove. The next best evidence is that it is still
            # alive a moment after boot, which catches a missing token or an
            # import error -- the two ways this one has actually failed.
            time.sleep(2.0)
            if pid_alive(record.pid):
                print(f"{spec.name}: pid {record.pid} running (no port; socket mode)")
            else:
                failures.append(spec.name)
                print(f"{spec.name}: exited immediately", file=sys.stderr)
                print(tail(Path(record.log_path)), file=sys.stderr)

    if failures:
        print(f"failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return cmd_status(repo_root, as_json=False, names=names)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="repository root (default: this checkout)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    def with_services(parser_):
        parser_.add_argument(
            "services",
            nargs="*",
            default=[],
            help="limit to these services (default: all three)",
        )
        return parser_

    p_status = with_services(
        sub.add_parser("status", help="report each service, and whether it runs current code")
    )
    p_status.add_argument("--json", action="store_true", dest="as_json")
    p_start = with_services(sub.add_parser("start", help="start anything not already healthy"))
    p_start.add_argument(
        "--reclaim",
        action="store_true",
        help="signal whatever already holds the ports instead of refusing",
    )
    with_services(sub.add_parser("stop", help="stop what the state file records; idempotent"))
    p_restart = with_services(sub.add_parser("restart", help="stop then start"))
    p_restart.add_argument("--reclaim", action="store_true")

    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    names = tuple(getattr(args, "services", ()) or ())

    if args.command == "status":
        return cmd_status(repo_root, as_json=args.as_json, names=names)
    if args.command == "start":
        return cmd_start(repo_root, reclaim=args.reclaim, names=names)
    if args.command == "stop":
        return cmd_stop(repo_root, names=names)
    if args.command == "restart":
        cmd_stop(repo_root, names=names)
        return cmd_start(repo_root, reclaim=args.reclaim, names=names)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
