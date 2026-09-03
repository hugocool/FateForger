"""What code this tmbx process is actually running, stated by the process itself.

On 2026-09-02 a live Slack session was diagnosed against `src/tmbx` as it read
in the editor, while nothing said which `src/tmbx` the server answering
`plan_read` had imported. The demo supervisor records a sha and a source
fingerprint when *it* starts a process -- but a server started by hand, or from
another checkout, leaves no such record, and the bot's own startup log named
only the bot's sha (#255).

So the server computes its identity itself, at import, from the tree it was
imported from, and reports it two ways: a log line on every start, and a
resource a client can read. The fingerprint is over file contents, not the git
sha: a checkout with 558 uncommitted lines in `ops.py` has a truthful sha and
is running none of it, which is exactly the case this exists to expose.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: The package this module lives in. Fingerprinting it, rather than the whole
#: repository, keeps an edit to the Slack bot from reading as a change to tmbx.
PACKAGE_ROOT = Path(__file__).resolve().parent

RESOURCE_URI = "tmbx://build/identity"


@dataclass(frozen=True)
class BuildIdentity:
    """Which sources a tmbx process imported, and when it said so."""

    git_sha: str | None
    source_fingerprint: str
    package_root: str
    started_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> BuildIdentity | None:
        """Parse what a server reported, or None when it reported nothing usable.

        Keys are identifiers this module mints, so reading them is a field
        lookup. A server too old to publish the resource answers nothing at
        all, and that absence must stay visible to the caller rather than be
        dressed up as an identity.
        """
        if not isinstance(payload, dict):
            return None
        fingerprint = payload.get("source_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        sha = payload.get("git_sha")
        return cls(
            git_sha=sha if isinstance(sha, str) and sha else None,
            source_fingerprint=fingerprint,
            package_root=str(payload.get("package_root") or ""),
            started_at=str(payload.get("started_at") or ""),
        )


def python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def fingerprint_sources(root: Path) -> str:
    """SHA-256 over the relative paths and contents of every ``.py`` under ``root``.

    Relative paths, deliberately: the same bytes checked out under a worktree
    and under the main checkout are the same code, and a bot comparing its own
    `src/tmbx` against the server's must not see a mismatch that is only a
    directory name. (The supervisor's fingerprint hashes absolute paths, which
    is right for its question -- "did this root move?" -- and wrong for ours.)
    Contents, not mtimes: a touch that changes nothing is not a redeploy.
    """
    digest = hashlib.sha256()
    for path in python_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\x00")
    return digest.hexdigest()


def git_head(root: Path) -> str | None:
    """HEAD of whichever repository ``root`` sits in, or None when there is none.

    Best effort on purpose: a missing git or a bare export must not stop the
    server from starting, and the fingerprint is the field that carries the
    real answer anyway.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def current_build_identity(
    package_root: Path = PACKAGE_ROOT, *, now: datetime | None = None
) -> BuildIdentity:
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return BuildIdentity(
        git_sha=git_head(package_root),
        source_fingerprint=fingerprint_sources(package_root),
        package_root=str(package_root),
        started_at=stamp,
    )


def describe(identity: BuildIdentity) -> str:
    """One line for a log: the short sha and a prefix of the fingerprint."""
    sha = (identity.git_sha or "unknown")[:9]
    return f"sha={sha} fingerprint={identity.source_fingerprint[:12]}"


__all__ = [
    "PACKAGE_ROOT",
    "RESOURCE_URI",
    "BuildIdentity",
    "current_build_identity",
    "describe",
    "fingerprint_sources",
    "git_head",
    "python_files",
]
