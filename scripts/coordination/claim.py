#!/usr/bin/env python3
"""Acquire, release and inspect peer-session claims (#369, #371).

    claim.py whoami
    claim.py acquire 371 --intent "building the release hook and sweeper"
    claim.py list
    claim.py release 371

A claim is the git ref ``refs/claims/<issue>``. Creating it twice returns 422
``Reference already exists``, so exactly one caller wins and the loser is told.

Run with the repo's own python; it needs no venv and no PYTHONPATH.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import claim_ledger as LEDGER  # noqa: E402
import claim_lib as L  # noqa: E402


def cmd_whoami(args: argparse.Namespace) -> int:
    ident = L.identify()
    print(json.dumps(ident.payload_holder(), indent=2, sort_keys=True))
    return 0


def cmd_acquire(args: argparse.Namespace) -> int:
    repo = args.repo or L.default_repo()
    ident = L.identify()
    payload = L.build_payload(
        ident,
        args.issue,
        repo,
        args.intent,
        ttl_hours=args.ttl_hours,
        release_on_idle=args.release_on_idle,
    )
    outcome, res = L.create_claim(repo, args.issue, payload, args.namespace)
    if outcome == "won":
        sha = (res.body or {}).get("object", {}).get("sha") if isinstance(res.body, dict) else None
        LEDGER.record_claim(
            ident.pid,
            f"{args.namespace}/{args.issue}",
            {
                "repo": repo,
                "issue": str(args.issue),
                "namespace": args.namespace,
                "object_sha": sha,
                "claimed_at": payload["claimed_at"],
                "expires_at": payload["expires_at"],
                "release_on_idle": args.release_on_idle,
                "intent": args.intent,
            },
        )
        print(f"claimed {L.ref_path(args.issue, args.namespace)} (blob {sha})")
        print("  echo it to humans with a comment on the issue -- once, never polled.")
        return 0
    if outcome == "taken":
        print(f"NOT yours: {L.ref_path(args.issue, args.namespace)} already exists.")
        print("  read the holder with:  claim.py list")
        print("  then follow the takeover ladder in the sweeper's output.")
        return 3
    print(f"error creating claim: HTTP {res.status}: {res.stderr}", file=sys.stderr)
    return 1


def cmd_release(args: argparse.Namespace) -> int:
    repo = args.repo or L.default_repo()
    key = f"{args.namespace}/{args.issue}"
    expect = None
    pid = None
    try:
        ident = L.identify()
        pid = ident.pid
        entry = LEDGER.load(pid)["claims"].get(key)
        if entry:
            expect = entry.get("object_sha")
    except L.ClaimError:
        pass
    if args.force:
        expect = None
    outcome, res = L.delete_claim(repo, args.issue, args.namespace, expect_sha=expect)
    if pid is not None and outcome in ("deleted", "absent"):
        LEDGER.forget_claim(pid, key)
    if outcome == "deleted":
        print(f"released {L.ref_path(args.issue, args.namespace)}")
        return 0
    if outcome == "absent":
        print(f"already gone: {L.ref_path(args.issue, args.namespace)}")
        return 0
    if outcome == "moved":
        print(
            f"refusing: {L.ref_path(args.issue, args.namespace)} now points at a "
            "different object -- somebody else holds it. Re-read before acting; "
            "--force overrides.",
            file=sys.stderr,
        )
        return 3
    print(f"error releasing: HTTP {res.status}: {res.stderr}", file=sys.stderr)
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    repo = args.repo or L.default_repo()
    claims = L.read_claims(repo, args.namespace)
    if args.json:
        print(json.dumps(claims, indent=2, sort_keys=True))
        return 0
    if not claims:
        print(f"no claims under refs/{args.namespace}/")
        return 0
    for c in claims:
        p = c.get("payload") or {}
        h = p.get("holder") or {}
        print(f"#{c['issue']:<6} {h.get('session_name') or '?':<16} pid={h.get('pid')} "
              f"expires={p.get('expires_at')}")
        print(f"        {p.get('intent') or c.get('payload_error') or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None, help="owner/name (default: this checkout's)")
    ap.add_argument("--namespace", default=L.DEFAULT_NAMESPACE,
                    help="ref namespace; use claimprobe for tests")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)

    a = sub.add_parser("acquire")
    a.add_argument("issue")
    a.add_argument("--intent", required=True,
                   help="one line: what you are doing. A hint, allowed to go stale.")
    a.add_argument("--ttl-hours", type=float, default=L.DEFAULT_TTL_HOURS)
    a.add_argument("--release-on-idle", action="store_true",
                   help="let the Stop hook drop this claim when the turn ends. "
                        "For one-shot sessions only: Stop fires EVERY turn.")
    a.set_defaults(fn=cmd_acquire)

    r = sub.add_parser("release")
    r.add_argument("issue")
    r.add_argument("--force", action="store_true",
                   help="delete even if the ref moved since we claimed it")
    r.set_defaults(fn=cmd_release)

    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except L.ClaimError as exc:
        print(f"claim error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
