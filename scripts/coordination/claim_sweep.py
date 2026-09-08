#!/usr/bin/env python3
"""Sweep stranded claims: the half that covers the cases where no hook runs.

    claim_sweep.py                 # report only -- reads nothing but refs and ps
    claim_sweep.py --apply         # additionally delete claims PROVEN dead
    claim_sweep.py --json

A hook can only release a claim when the agent exits in an orderly way. The
failure modes that actually strand a claim -- SIGKILL, a closed laptop, a
terminal abandoned mid-turn, a context-exhausted session that auto-compacts
instead of ending -- are exactly the ones where no hook fires. This is what
covers those, and it is also the only thing that covers a Codex holder, which
has neither a session-registry entry nor an address.

**The sweeper acts only on proof.** Rung 1 of the takeover ladder (#369) --
a dead pid -- is decidable from ``ps`` alone, so the sweeper does it. Rungs 2-4
need somebody to *ask* the holder and then wait, which a shell script cannot do
and should not fake. Those are printed as a work list for the agent that ran the
sweep, with the address to message. Reporting a takeover you did not verify is
worse than not sweeping.

Costs 1 REST call to list plus 1 per claim. Run it when you want to know; never
poll it. All 49 sessions authenticate as one user and share one bucket.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import claim_ledger as LEDGER  # noqa: E402
import claim_lib as L  # noqa: E402

# A live process that has not finished a turn in this long is a session nobody
# is driving. It is a *suspicion*, never a proof: the sweeper reports it and
# takes nothing. Tunable, like the TTL (#369 left both to be measured).
DEFAULT_IDLE_HOURS = 6.0

# Verdicts, in the order a reader cares about them.
ORPHANED = "orphaned"          # proof: holder process is gone. Rung 1: take it.
IDLE_SUSPECT = "idle-suspect"  # alive but nobody is driving it. Rung 2: ask it.
EXPIRED_REMOTE = "expired-remote"  # off-machine and past TTL. Rung 2/3/4.
HELD = "held"                  # holder is live here. Leave it alone.
HELD_REMOTE = "held-remote"    # off-machine, within TTL. Leave it alone.
UNREADABLE = "unreadable"      # payload we cannot parse. Never touch it.


def classify(
    claim: dict,
    ps_table: dict[int, float],
    ledgers: dict[int, dict],
    now: _dt.datetime,
    idle_hours: float,
) -> dict:
    """One claim -> a verdict plus the evidence behind it."""
    out = {
        "ref": claim["ref"],
        "issue": claim["issue"],
        "object_sha": claim["object_sha"],
        "verdict": UNREADABLE,
        "evidence": claim.get("payload_error") or "no payload",
        "holder": None,
        "intent": None,
        "next_step": "leave alone; a payload this sweeper cannot read may be a "
                     "newer schema. Ask Hugo.",
    }
    payload = claim.get("payload")
    if not isinstance(payload, dict):
        return out
    holder = payload.get("holder") or {}
    out["holder"] = holder
    out["intent"] = payload.get("intent")

    expires_at = payload.get("expires_at")
    expired = False
    if expires_at:
        try:
            expired = L.parse_iso(expires_at) < now
        except ValueError:
            expired = False

    same_machine = holder.get("machine_id") == L.machine_id()
    pid = holder.get("pid")

    if not same_machine:
        addr = holder.get("session_name") or holder.get("session_id") or "?"
        if expired:
            out["verdict"] = EXPIRED_REMOTE
            out["evidence"] = (
                f"holder is on machine {holder.get('machine_id')} "
                f"({holder.get('hostname')}), which this sweeper cannot probe; "
                f"TTL expired at {expires_at}"
            )
            out["next_step"] = (
                f"rung 2: message '{addr}'. An answer means it is alive -- renew, "
                "do not take. No answer within the wait window -> rung 3, take it "
                "and record the evidence on the issue. If the holder is Codex it "
                "is not addressable at all: the TTL is the only signal, so rung 4 "
                "-- escalate to Hugo."
            )
        else:
            out["verdict"] = HELD_REMOTE
            out["evidence"] = f"off-machine holder, TTL good until {expires_at}"
            out["next_step"] = "leave alone"
        return out

    if not isinstance(pid, int):
        out["verdict"] = UNREADABLE
        out["evidence"] = "claim says this machine but carries no pid"
        return out

    verdict, evidence = L.liveness(
        pid, holder.get("proc_start_epoch"), holder.get("proc_start"), ps_table
    )
    if verdict in ("dead", "recycled"):
        out["verdict"] = ORPHANED
        out["evidence"] = evidence
        out["next_step"] = (
            "rung 1: proof. --apply deletes the ref; record the takeover and this "
            "evidence in a comment on the issue."
        )
        return out
    if verdict == "unknown":
        out["verdict"] = UNREADABLE
        out["evidence"] = evidence
        return out

    # Alive. Is anybody driving it?
    led = ledgers.get(pid) or {}
    last_stop = led.get("last_stop_at")
    idle_note = "no Stop-hook stamp (hook not installed, or no turn finished yet)"
    if last_stop:
        try:
            age_h = (now - L.parse_iso(last_stop)).total_seconds() / 3600.0
            idle_note = f"last finished a turn {age_h:.1f}h ago"
            if age_h > idle_hours:
                out["verdict"] = IDLE_SUSPECT
                out["evidence"] = f"{evidence}; {idle_note}"
                out["next_step"] = (
                    f"rung 2: the process lives but {idle_note}. Message "
                    f"'{holder.get('session_name')}' "
                    f"(session {holder.get('session_id')}). Never take this on "
                    "idle age alone -- a live process is not a stale claim."
                )
                return out
        except ValueError:
            pass
    out["verdict"] = HELD
    out["evidence"] = f"{evidence}; {idle_note}"
    out["next_step"] = "leave alone"
    return out


def sweep(repo: str, namespace: str, idle_hours: float, apply: bool) -> dict:
    remaining = L.rest_remaining()
    if remaining is not None and remaining < L.REST_BUDGET_FLOOR:
        raise L.ClaimError(
            f"REST budget is down to {remaining}; all sessions share one bucket, "
            f"so this sweep is refusing to spend it. Floor is {L.REST_BUDGET_FLOOR}."
        )
    claims = L.read_claims(repo, namespace)
    ps_table = L.ps_start_times()
    ledgers = LEDGER.all_ledgers()
    now = L.now_utc()

    rows = [classify(c, ps_table, ledgers, now, idle_hours) for c in claims]

    actions = []
    if apply:
        for row in rows:
            if row["verdict"] != ORPHANED:
                continue
            # Compare-and-delete: re-read the ref immediately before deleting so
            # a claim recreated since we listed is not clobbered. This NARROWS
            # the window; GitHub's ref DELETE has no If-Match, so it does not
            # close it. Concurrent sweepers are otherwise harmless -- the second
            # one gets "absent" and reports it.
            outcome, res = L.delete_claim(
                repo, row["issue"], namespace, expect_sha=row["object_sha"]
            )
            row["applied"] = outcome
            actions.append({"ref": row["ref"], "outcome": outcome})
            if outcome == "moved":
                row["evidence"] += (
                    " -- but the ref moved between listing and deleting; a fresh "
                    "claim now holds it and was left alone"
                )
    return {
        "repo": repo,
        "namespace": namespace,
        "swept_at": L.iso(now),
        "rest_remaining_before": remaining,
        "machine_id": L.machine_id(),
        "claims": rows,
        "applied": actions,
        "apply": apply,
    }


ORDER = [ORPHANED, IDLE_SUSPECT, EXPIRED_REMOTE, UNREADABLE, HELD, HELD_REMOTE]


def render(result: dict) -> str:
    rows = sorted(
        result["claims"],
        key=lambda r: (ORDER.index(r["verdict"]) if r["verdict"] in ORDER else 99),
    )
    lines = [
        f"claims under refs/{result['namespace']}/ in {result['repo']} "
        f"-- {len(rows)} claim(s), swept {result['swept_at']}",
        f"REST remaining before sweep: {result['rest_remaining_before']}",
        "",
    ]
    if not rows:
        lines.append("  (none)")
    for r in rows:
        h = r.get("holder") or {}
        lines.append(
            f"  [{r['verdict']:<14}] #{r['issue']:<6} "
            f"{h.get('session_name') or '?'} pid={h.get('pid')} "
            f"{h.get('hostname') or ''}"
        )
        if r.get("intent"):
            lines.append(f"        intent: {r['intent']}")
        lines.append(f"        why:    {r['evidence']}")
        if r["verdict"] not in (HELD, HELD_REMOTE):
            lines.append(f"        do:     {r['next_step']}")
        if r.get("applied"):
            lines.append(f"        applied: {r['applied']}")
        lines.append("")
    if not result["apply"]:
        n = sum(1 for r in rows if r["verdict"] == ORPHANED)
        lines.append(
            f"report only. {n} claim(s) are provably orphaned; re-run with --apply "
            "to delete those refs. Nothing else is ever deleted automatically."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None)
    ap.add_argument("--namespace", default=L.DEFAULT_NAMESPACE)
    ap.add_argument("--idle-hours", type=float, default=DEFAULT_IDLE_HOURS)
    ap.add_argument("--apply", action="store_true",
                    help="delete refs whose holder is PROVEN gone. Nothing else.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        repo = args.repo or L.default_repo()
        result = sweep(repo, args.namespace, args.idle_hours, args.apply)
    except L.ClaimError as exc:
        print(f"sweep error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
