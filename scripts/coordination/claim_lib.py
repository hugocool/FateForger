"""Shared plumbing for the peer-session claim protocol (#369, #371).

A claim is the git ref ``refs/claims/<issue>`` in the project repo, created via
``POST /repos/{owner}/{repo}/git/refs``. A second creation of the same ref
returns 422 ``Reference already exists``, so exactly one caller wins and the
loser is told it lost. Exclusivity forces the ref *name*, which is why the
holder's identity lives in the object the ref points at rather than in the name:
claiming is create-blob-then-create-ref.

Nothing in here interprets what a human wrote. The only string handling is over
identifiers this system or the OS minted -- ref names, session UUIDs, pids, and
``ps`` timestamps -- which the project's no-pattern-matching rule explicitly
carves out.

Stdlib only, plus the ``gh`` CLI for authentication. Hooks must not need a venv.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import pathlib
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

# --- operational constants -------------------------------------------------
# Both of these are the "two numbers left to tune" from #369. They are named
# here so the coordination skill (#372) can change them without touching the
# protocol. The TTL is deliberately long: liveness on this machine is free and
# exact, so the TTL only has to cover holders we cannot probe, and a short TTL
# would buy renewal writes against a budget of ~10 content writes per agent per
# hour shared by 49 sessions.
DEFAULT_TTL_HOURS = 24
# How far apart two readings of the same process's start time may be before we
# call it a different process. ``ps`` prints whole seconds; the registry records
# whole seconds; so anything above 1s of slack is generosity.
PROC_START_TOLERANCE_S = 2.0
# The sweeper refuses to run when the shared REST budget is this thin. All 49
# sessions authenticate as one user and share one bucket.
REST_BUDGET_FLOOR = 500

SESSION_REGISTRY = pathlib.Path.home() / ".claude" / "sessions"
DEFAULT_NAMESPACE = "claims"
PAYLOAD_SCHEMA = "fateforger.claim/1"

# ``ps -o lstart=`` and ``~/.claude/sessions/<pid>.json``'s procStart use the
# same layout. They do NOT use the same timezone -- see procstart_epoch_utc.
_PROC_TIME_FORMAT = "%a %b %d %H:%M:%S %Y"


class ClaimError(RuntimeError):
    """Anything that should stop a claim operation with a readable message."""


# --- time, and the trap ----------------------------------------------------

def ps_lstart_epoch(lstart: str) -> float:
    """Epoch seconds for a ``ps -o lstart=`` string, which is in LOCAL time."""
    naive = _dt.datetime.strptime(lstart.strip(), _PROC_TIME_FORMAT)
    return naive.astimezone().timestamp() if naive.tzinfo else naive.timestamp()


def procstart_epoch_utc(proc_start: str) -> float:
    """Epoch seconds for the session registry's ``procStart``, which is UTC.

    This is the trap that produced a confident and entirely wrong "48 stale"
    reading. The registry writes ``procStart`` in UTC and ``ps -o lstart=``
    prints local time, in the *same* string layout, so comparing the two as
    strings disagrees for every process outside UTC and agrees for none.
    Measured on this machine 2026-09-07 across all 48 registry entries: naive
    string equality matched 0 of 48; parsing the registry value as UTC and the
    ``ps`` value as local matched all 48 to 0.000s.
    """
    naive = _dt.datetime.strptime(proc_start.strip(), _PROC_TIME_FORMAT)
    return naive.replace(tzinfo=_dt.timezone.utc).timestamp()


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> _dt.datetime:
    return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc
    )


# --- process liveness ------------------------------------------------------

def process_table() -> dict[int, dict[str, Any]]:
    """One ``ps`` sweep: {pid: {"ppid": int, "start": epoch-seconds}}.

    One call for the whole table -- the sweeper looks at many pids and the
    ancestry walk needs ppids, and GitHub is not the only budget worth keeping.
    """
    out = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,lstart="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    table: dict[int, dict[str, Any]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
            start = ps_lstart_epoch(parts[2])
        except ValueError:
            continue
        table[pid] = {"ppid": ppid, "start": start}
    return table


def ps_start_times(pids: Iterable[int] | None = None) -> dict[int, float]:
    """{pid: epoch-seconds-of-process-start} for live processes."""
    table = {pid: row["start"] for pid, row in process_table().items()}
    if pids is not None:
        wanted = set(pids)
        return {p: t for p, t in table.items() if p in wanted}
    return table


def liveness(
    pid: int,
    expected_epoch: float | None,
    proc_start: str | None,
    ps_table: dict[int, float],
) -> tuple[str, str]:
    """Return ``(verdict, evidence)`` for one holder on THIS machine.

    Verdicts: ``alive`` | ``dead`` | ``recycled`` | ``unknown``.

    ``dead`` and ``recycled`` are both proof the holder is gone -- ``recycled``
    means some other process now owns the pid, which is exactly the case a bare
    ``kill -0`` would get wrong. ``unknown`` means the claim carried no usable
    start time, so we must not conclude anything.
    """
    if expected_epoch is None and proc_start:
        try:
            expected_epoch = procstart_epoch_utc(proc_start)
        except ValueError:
            expected_epoch = None
    if expected_epoch is None:
        return "unknown", "claim carried no comparable process start time"
    observed = ps_table.get(pid)
    if observed is None:
        return "dead", f"no live process with pid {pid}"
    delta = abs(observed - expected_epoch)
    if delta <= PROC_START_TOLERANCE_S:
        return "alive", f"pid {pid} start time matches to {delta:.3f}s"
    return (
        "recycled",
        f"pid {pid} is live but started {delta:.0f}s away from the claim's "
        f"record -- the pid was reused",
    )


# --- who am I --------------------------------------------------------------

@dataclass
class SessionIdentity:
    session_id: str
    session_name: str | None
    pid: int
    proc_start: str | None
    proc_start_epoch: float | None
    messaging_socket: str | None
    cwd: str | None
    harness: str = "claude-code"

    def payload_holder(self) -> dict[str, Any]:
        return {
            "harness": self.harness,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "messaging_socket": self.messaging_socket,
            "pid": self.pid,
            "proc_start": self.proc_start,
            "proc_start_epoch": self.proc_start_epoch,
            "machine_id": machine_id(),
            "hostname": socket.gethostname(),
            "worktree": self.cwd or os.getcwd(),
        }


def read_registry() -> list[dict[str, Any]]:
    """Every ``~/.claude/sessions/<pid>.json`` the harness has written."""
    if not SESSION_REGISTRY.is_dir():
        return []
    rows = []
    for path in sorted(SESSION_REGISTRY.glob("*.json")):
        try:
            rows.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return rows


def identify(session_id: str | None = None) -> SessionIdentity:
    """Self-identify without being told who we are.

    ``CLAUDE_CODE_SESSION_ID`` is set in every hook and Bash subprocess, so a
    script always knows its session id. What it does **not** get for free is its
    *process*, and the process is what liveness is about.

    Measured on this checkout 2026-09-07: **48 live claude processes carry only
    25 distinct sessionIds.** 13 session ids had 2-4 live registry entries
    apiece -- resuming a conversation in a new window starts a new process under
    the same session id and leaves the old one running. So scanning the registry
    for ``sessionId == CLAUDE_CODE_SESSION_ID`` and taking a match is a coin
    flip between live processes, and a claim that records the wrong one is
    released the moment the *other* window is closed.

    The reliable join is the other direction: walk up our own process ancestry
    to the nearest pid that has a registry entry. That pid is, by construction,
    the process actually running us. The session id is then a consistency check
    rather than the lookup key.
    """
    env_session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    session_id = session_id or env_session
    procs = process_table()
    by_pid = {int(r["pid"]): r for r in read_registry() if "pid" in r}

    row = None
    pid = os.getpid()
    for _ in range(12):
        if pid in by_pid:
            row = by_pid[pid]
            break
        parent = procs.get(pid, {}).get("ppid")
        if not parent or parent == pid:
            break
        pid = parent

    if row is None:
        # No ancestor is registered (a nested wrapper, a detached shell). Fall
        # back to the session id, preferring the newest LIVE entry -- still
        # ambiguous, so say so rather than pretending otherwise.
        candidates = [
            r
            for r in by_pid.values()
            if r.get("sessionId") == session_id and int(r["pid"]) in procs
        ]
        if not candidates:
            raise ClaimError(
                f"no ancestor process and no live registry entry for session "
                f"{session_id} under {SESSION_REGISTRY}; a claim made now could "
                "not be probed for liveness"
            )
        row = max(candidates, key=lambda r: r.get("startedAt", 0))

    pid = int(row["pid"])
    if session_id and row.get("sessionId") != session_id:
        # Not fatal: the ancestry answer is the better one. Record both.
        session_id = row.get("sessionId") or session_id
    session_id = session_id or row.get("sessionId")

    proc_start = row.get("procStart")
    # Self-calibrating: take the epoch from THIS machine's own ps rather than
    # from the registry string, so nothing downstream has to assume which
    # timezone the registry was written in.
    observed = procs.get(pid, {}).get("start")
    if observed is None and proc_start:
        observed = procstart_epoch_utc(proc_start)
    return SessionIdentity(
        session_id=session_id,
        session_name=row.get("name"),
        pid=pid,
        proc_start=proc_start,
        proc_start_epoch=observed,
        messaging_socket=row.get("messagingSocketPath"),
        cwd=row.get("cwd"),
    )


def machine_id() -> str:
    """A stable id for this machine, short enough to eyeball in a payload."""
    raw = None
    if platform.system() == "Darwin" and shutil.which("ioreg"):
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        for line in out.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().strip('"') == "IOPlatformUUID":
                raw = value.strip().strip('"')
                break
    if not raw:
        raw = socket.gethostname()
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# --- GitHub ----------------------------------------------------------------

def default_repo() -> str:
    env = os.environ.get("CLAIM_REPO")
    if env:
        return env
    out = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=False,
    )
    name = out.stdout.strip()
    if not name:
        raise ClaimError("could not determine the repo; set CLAIM_REPO=owner/name")
    return name


@dataclass
class GhResult:
    ok: bool
    status: int
    body: Any
    stderr: str = ""


def gh_api(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> GhResult:
    """One ``gh api`` call. Never raises on an HTTP error; returns the status.

    422 and 404 are protocol answers here (``Reference already exists``, and a
    ref another sweeper already deleted), not failures.
    """
    cmd = ["gh", "api", "--method", method, path]
    if payload is not None:
        cmd += ["--input", "-"]
    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return GhResult(False, 0, None, f"{type(exc).__name__}: {exc}")
    body: Any = None
    if proc.stdout.strip():
        try:
            body = json.loads(proc.stdout)
        except ValueError:
            body = proc.stdout
    status = 200 if proc.returncode == 0 else _status_from_stderr(proc.stderr)
    return GhResult(proc.returncode == 0, status, body, proc.stderr.strip())


def _status_from_stderr(stderr: str) -> int:
    """Pull the HTTP status out of gh's own error line.

    gh writes ``gh: ... (HTTP 422)``. This parses gh's output format, not user
    content.
    """
    marker = "(HTTP "
    idx = stderr.find(marker)
    if idx < 0:
        return 0
    tail = stderr[idx + len(marker):]
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


def rest_remaining() -> int | None:
    res = gh_api("/rate_limit")
    if not res.ok or not isinstance(res.body, dict):
        return None
    try:
        return int(res.body["resources"]["core"]["remaining"])
    except (KeyError, TypeError, ValueError):
        return None


def ref_path(issue: int | str, namespace: str = DEFAULT_NAMESPACE) -> str:
    return f"refs/{namespace}/{issue}"


def create_claim(
    repo: str,
    issue: int | str,
    payload: dict[str, Any],
    namespace: str = DEFAULT_NAMESPACE,
) -> tuple[str, GhResult]:
    """Create blob then ref. Returns ``(outcome, result)``.

    Outcomes: ``won`` | ``taken`` | ``error``. ``taken`` is the 422 -- somebody
    else holds it, and the caller is *told*, which is the whole reason this is
    the arbiter.
    """
    blob = gh_api(
        f"/repos/{repo}/git/blobs",
        "POST",
        {"content": json.dumps(payload, indent=2, sort_keys=True), "encoding": "utf-8"},
    )
    if not blob.ok or not isinstance(blob.body, dict) or "sha" not in blob.body:
        return "error", blob
    ref = gh_api(
        f"/repos/{repo}/git/refs",
        "POST",
        {"ref": ref_path(issue, namespace), "sha": blob.body["sha"]},
    )
    if ref.ok:
        return "won", ref
    if ref.status == 422:
        return "taken", ref
    return "error", ref


def read_claims(repo: str, namespace: str = DEFAULT_NAMESPACE) -> list[dict[str, Any]]:
    """Every claim ref plus its payload. 1 call + 1 per claim. Never polled."""
    listing = gh_api(f"/repos/{repo}/git/matching-refs/{namespace}")
    if not listing.ok or not isinstance(listing.body, list):
        raise ClaimError(f"could not list refs/{namespace}: {listing.stderr}")
    claims = []
    for entry in listing.body:
        ref = entry.get("ref", "")
        sha = (entry.get("object") or {}).get("sha")
        record: dict[str, Any] = {
            "ref": ref,
            "issue": ref.rsplit("/", 1)[-1],
            "object_sha": sha,
            "payload": None,
            "payload_error": None,
        }
        blob = gh_api(f"/repos/{repo}/git/blobs/{sha}")
        if blob.ok and isinstance(blob.body, dict):
            import base64

            try:
                raw = base64.b64decode(blob.body.get("content", "")).decode()
                record["payload"] = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                record["payload_error"] = f"unreadable payload: {exc}"
        else:
            record["payload_error"] = blob.stderr or "blob unreadable"
        claims.append(record)
    return claims


def delete_claim(
    repo: str,
    issue: int | str,
    namespace: str = DEFAULT_NAMESPACE,
    expect_sha: str | None = None,
) -> tuple[str, GhResult]:
    """Delete a claim ref. Outcomes: ``deleted`` | ``absent`` | ``moved`` | ``error``.

    ``expect_sha`` narrows -- it cannot close -- the window where we read a dead
    holder's claim, the holder released, a fresh session claimed, and we then
    delete the fresh claim. GitHub's ref DELETE has no ``If-Match``, so this is
    a re-read immediately before the delete, not a compare-and-set. Say so out
    loud rather than implying atomicity we do not have.
    """
    if expect_sha is not None:
        current = gh_api(f"/repos/{repo}/git/ref/{ref_path(issue, namespace)[5:]}")
        if current.status == 404:
            return "absent", current
        if not current.ok:
            # We could not read it, which is not the same as it having moved.
            # Reporting "moved" here would tell the caller a fresh claim exists
            # when in fact we know nothing.
            return "error", current
        observed = (
            (current.body or {}).get("object", {}).get("sha")
            if isinstance(current.body, dict)
            else None
        )
        if observed != expect_sha:
            return "moved", current
    res = gh_api(f"/repos/{repo}/git/{ref_path(issue, namespace)}", "DELETE")
    if res.ok:
        return "deleted", res
    if res.status in (404, 422):
        return "absent", res
    return "error", res


def build_payload(
    identity: SessionIdentity,
    issue: int | str,
    repo: str,
    intent: str,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    release_on_idle: bool = False,
) -> dict[str, Any]:
    started = now_utc()
    return {
        "schema": PAYLOAD_SCHEMA,
        "repo": repo,
        "issue": str(issue),
        "holder": identity.payload_holder(),
        "claimed_at": iso(started),
        "expires_at": iso(started + _dt.timedelta(hours=ttl_hours)),
        # A hint, not a record. It is allowed to go stale; anything
        # authoritative is asked for rather than read (#369).
        "intent": intent,
        "release_on_idle": release_on_idle,
    }
