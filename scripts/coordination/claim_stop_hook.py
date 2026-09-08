#!/usr/bin/env python3
"""``Stop`` hook: the orderly half of claim release.

Bound to ``Stop``, not ``SessionEnd``. ``SessionEnd``'s documented reasons --
``clear``, ``resume``, ``logout``, ``prompt_input_exit``, ``other`` -- are all
orderly exits; it never fires on context exhaustion (that path auto-compacts);
and it is unreliable even on the happy path (anthropics/claude-code#79702,
re-reproduced on 2.1.251, where whether it fires depends on how long Stop hooks
keep the session alive).

**What this hook must not do, and why.** Verified against the official hook
reference: ``Stop`` fires *every time the main agent finishes responding* -- once
per turn, not once per session. So a hook that unconditionally dropped this
session's claims would give every claim a lifetime of one turn, and a session
driving a ticket across twenty turns would lose its claim after the first.
Two sessions both believing they hold an issue is a worse failure than a stale
ref, because a stale ref is cheap to detect (a dead pid is proof) and a double
grant is not detectable at all.

So the hook does two things, and neither of them guesses:

1. **Stamps a turn boundary** into the local ledger. Free, no network. This is
   the one fact only a Stop hook can observe: ``(pid, procStart)`` proves a
   process is *running*, but cannot tell a session working from one abandoned in
   a tab. The gap since the last stamp can, and the sweeper reads it.
2. **Releases claims that opted in** -- those created with ``--release-on-idle``,
   the mode for one-shot and automated sessions whose turn end really is the end
   of their work.

Everything else is the sweeper's job, which is the honest division: the hook
covers the case where the agent is still alive to be helpful, and the sweeper
covers the cases that actually strand claims.

Failure path, which the ticket asks for explicitly: this hook never fails the
session. Network down, ``gh`` unauthenticated, GitHub 500 -- all are caught, the
failure is written into the ledger where a later reader can see a release was
*attempted* and did not land, and the hook exits 0. The unreleased ref then
falls to the sweeper. It never exits 2 and never returns a blocking decision:
a Stop hook that blocks traps the session in a loop, and this one has nothing
worth stopping a turn for.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import claim_ledger as LEDGER  # noqa: E402
import claim_lib as L  # noqa: E402

TIMEOUT_S = 8.0


def main() -> int:
    notes: list[str] = []
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001 - a hook must never take the session down
        raw = ""
    event = {}
    if raw.strip():
        try:
            event = json.loads(raw)
        except ValueError:
            event = {}

    # Guard the documented loop hazard even though we never block: if a Stop
    # hook is already keeping this turn alive, do the least possible.
    if event.get("stop_hook_active"):
        return 0

    try:
        ident = L.identify(event.get("session_id"))
    except Exception as exc:  # noqa: BLE001
        # No registry entry means no claim could have been made from here.
        _emit([f"claim hook: could not self-identify ({exc}); nothing to release"],
              quiet=True)
        return 0

    try:
        LEDGER.stamp_idle(
            ident.pid, L.iso(L.now_utc()), ident.session_id, ident.session_name
        )
    except Exception as exc:  # noqa: BLE001
        notes.append(f"claim hook: could not stamp idle time ({exc})")

    try:
        held = LEDGER.load(ident.pid)["claims"]
    except Exception:  # noqa: BLE001
        held = {}

    # The common case: nothing claimed, zero network calls. With ~49 sessions
    # sharing one rate-limit bucket, a per-turn hook that called GitHub
    # unconditionally would be the most expensive thing in the fleet.
    to_release = {k: v for k, v in held.items() if v.get("release_on_idle")}
    if not to_release:
        _emit(notes, quiet=True)
        return 0

    for key, entry in to_release.items():
        repo = entry.get("repo")
        issue = entry.get("issue")
        namespace = entry.get("namespace", L.DEFAULT_NAMESPACE)
        try:
            outcome, res = L.delete_claim(
                repo, issue, namespace, expect_sha=entry.get("object_sha")
            )
        except Exception as exc:  # noqa: BLE001
            outcome, res = "error", L.GhResult(False, 0, None, str(exc))
        if outcome in ("deleted", "absent"):
            LEDGER.forget_claim(ident.pid, key)
            notes.append(f"claim hook: released refs/{namespace}/{issue} ({outcome})")
        else:
            entry["release_failed_at"] = L.iso(L.now_utc())
            entry["release_failure"] = f"{outcome}: HTTP {res.status} {res.stderr}"[:400]
            try:
                LEDGER.record_claim(ident.pid, key, entry)
            except Exception:  # noqa: BLE001
                pass
            notes.append(
                f"claim hook: could NOT release refs/{namespace}/{issue} "
                f"({entry['release_failure']}). The sweeper will reclaim it once "
                "this process exits; nothing is lost, but say so if you meant to "
                "hand the issue over now."
            )
    _emit(notes)
    return 0


def _emit(notes: list[str], quiet: bool = False) -> None:
    """Say something to the transcript only when there is something to say."""
    if not notes:
        return
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": " | ".join(notes),
        },
        "suppressOutput": quiet,
    }
    print(json.dumps(payload))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - last-resort guard
        print(f"claim hook: unexpected failure, ignored: {exc}", file=sys.stderr)
        raise SystemExit(0)
