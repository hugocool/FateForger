"""Link the durable rules that predate the anchor graph to the anchors their
observations already name (#290).

`reproject` cannot do this: anchors are written to the append-only log at
ingest and re-projection re-asks projection only (I2). The split path already
relinks from observation anchor names; this is the same three calls as a
one-off pass. Resolving a name to an anchor is a judgement, so it goes to the
judge even on a dry run; nothing is minted (`max_new=0`): a name the judge
cannot place on an existing anchor is reported, not created.

Each of a rule's names is asked on its own call. Over a list `resolve_anchors`
is all-or-nothing — the first name the judge calls new forfeits every name in
it that a real anchor already covers — and the rule should link to whatever
did resolve. The calls are issued together with `asyncio.gather`, but be clear
about what that buys: `resolve_anchors` holds a per-store `asyncio.Lock`
across the judge await (`anchoring.py:54-56`), so they run one after another
regardless. The gather is the right shape and the serialisation is the lock's,
not this script's; widening it belongs in `anchoring.py`, not here.

A name the judge resolves to a uid that names no anchor is re-asked up to
`MAX_UID_ATTEMPTS` times. The judge mistypes a hex character of an otherwise
correct uid often enough to lose links to it; the guard that catches this stays
exactly as strict, the question is simply put again.

Run on a copy first, then on the live store; `--apply` backs the store up
before writing. Links only: no observation, no projection, no other field.
Under `--apply` a transport failure aborts the loop with the rules already
linked committed — each `replace_constraint_links` is its own commit — so a
re-run resumes rather than repeating: a rule that already has anchors is
skipped.

A dry run must not write, so it refuses a store the code would migrate: the
store constructors call `apply_migrations`, which stamps and commits DDL on
anything behind `SCHEMA_VERSION`. The version is read first, read-only, and a
mismatch without `--apply` is an exit, not a silent schema change.

    PYTHONPATH=src python scripts/memory/relink_anchors.py /path/copy.db
    PYTHONPATH=src python scripts/memory/relink_anchors.py data/memory.db --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint_store import ConstraintStore
from memory.judge import Judge
from memory.migrations import SCHEMA_VERSION
from memory.openrouter_judge import OpenRouterJudge
from memory.store import ObservationStore


# How many times one name may be re-asked when the judge answers with a uid
# that names no anchor. Measured on this corpus: the judge resolves `lunch` to
# the right anchor but mistypes one hex character of its uid in roughly three
# draws of four, so a single draw loses a link that is plainly correct. Three
# draws is the ceiling for a one-off pass; the underlying transcription bug is
# ticketed against the memory server, and the guard itself stays as strict as
# it was — an invented uid is never accepted, only re-asked.
MAX_UID_ATTEMPTS = 3

# The unknown-uid guard's own words (`anchoring.resolve_anchors`). This is not
# a judgement about user content: it is this system's error text deciding which
# of two failures we are looking at — a uid this system minted was mistyped
# (retryable), or the judge says the name is new (deterministic, not retried).
# The would-mint refusal is a decision, not a transcription slip, so re-asking
# it would only spend calls to hear the same answer.
_UNKNOWN_UID = "unknown anchor_uid"


@dataclass
class NameOutcome:
    """What became of one anchor name."""

    name: str
    uid: str | None = None
    attempts: int = 0
    error: str = ""


@dataclass
class RelinkReport:
    uid: str
    name: str
    names: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    unresolved_names: list[str] = field(default_factory=list)
    outcomes: list[NameOutcome] = field(default_factory=list)
    #: "linked" (or "would link" on a dry run) when at least one name resolved,
    #: "no names" for a rule whose observations name no anchor, "unresolved"
    #: when every name failed
    action: str = ""


async def _resolve_one(name: str, anchor_store: AnchorStore, judge: Judge) -> NameOutcome:
    """Resolve a single name, re-asking only a mistyped uid.

    One name per call, because `resolve_anchors` is all-or-nothing: over a list,
    the first name the judge calls new forfeits every name in it that a real
    anchor already covers. On this corpus that cost *Evening Ritual* its
    `dinner`, `shower` and `evening shutdown ritual` links for the sake of three
    names nothing has minted.
    """
    outcome = NameOutcome(name=name)
    for attempt in range(1, MAX_UID_ATTEMPTS + 1):
        outcome.attempts = attempt
        try:
            uids = await resolve_anchors([name], anchor_store, judge, max_new=0)
        except ValueError as exc:
            outcome.error = str(exc)
            if _UNKNOWN_UID in outcome.error:
                continue
            return outcome
        if not uids:
            # One name in, `max_new=0`: `resolve_anchors` either returns that
            # name's uid or raises — it cannot come back empty-handed, because
            # a name the judge does not place is a mint it refuses out loud.
            # If this ever fires, that contract changed and the caller below,
            # which reads uids[0], would be wrong in a quieter way.
            raise AssertionError(
                f"resolve_anchors returned no uid and did not raise for "
                f"{name!r}; its one-name contract has changed"
            )
        outcome.uid = uids[0]
        outcome.error = ""
        return outcome
    return outcome


def _names_for(
    constraint_uid: str, constraints: ConstraintStore, observations: ObservationStore
) -> list[str]:
    """The anchor names this rule's observations carry, in order, deduplicated."""
    names: list[str] = []
    for observation_uid in constraints.observations_for(constraint_uid):
        observation = observations.get(observation_uid)
        if observation is not None:
            names.extend(observation.anchors)
    return list(dict.fromkeys(names))


def _fold(report: RelinkReport, results: list[NameOutcome | BaseException]) -> None:
    """Record each name's outcome on the report and collect the uids."""
    resolved: list[str] = []
    for result in results:
        # Only ValueError is a per-name verdict, and `_resolve_one` has
        # already turned every one of those into an outcome. Anything
        # arriving here as an exception is transport or programmer error and
        # must stay loud rather than be filed as "this name did not resolve".
        if isinstance(result, BaseException):
            raise result
        report.outcomes.append(result)
        if result.uid is None:
            report.unresolved_names.append(result.name)
        else:
            resolved.append(result.uid)
    # Two names can be one anchor; the link table takes each uid once.
    report.resolved = list(dict.fromkeys(resolved))


async def relink(db_path: str, judge: Judge, *, apply: bool) -> list[RelinkReport]:
    observations = ObservationStore(db_path)
    constraints = ConstraintStore(db_path)
    anchors = AnchorStore(db_path)
    reports: list[RelinkReport] = []
    for constraint in constraints.durable():
        if anchors.anchors_for(constraint.uid):
            continue
        report = RelinkReport(uid=constraint.uid, name=constraint.name)
        report.names = _names_for(constraint.uid, constraints, observations)
        if not report.names:
            report.action = "no names"
            reports.append(report)
            continue
        # One rule's names are independent judgements, so they are issued
        # together. They do not actually overlap: `resolve_anchors` holds a
        # per-store lock across the judge await, which is what keeps two names
        # from each minting the same anchor. The gather is the right shape;
        # making it concurrent in fact is a change to `anchoring.py`.
        results = await asyncio.gather(
            *(_resolve_one(name, anchors, judge) for name in report.names),
            return_exceptions=True,
        )
        _fold(report, list(results))
        if not report.resolved:
            report.action = "unresolved"
            reports.append(report)
            continue
        if apply:
            anchors.replace_constraint_links(constraint.uid, report.resolved)
            report.action = "linked"
        else:
            report.action = "would link"
        reports.append(report)
    return reports


def _judge() -> Judge:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set; load the parent .env (see the plan)")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    # The pin is the decision record (CLAUDE.md); OpenRouterJudge's own default
    # names a model this project no longer uses, so name the pin explicitly
    # rather than inheriting a constructor default nobody reads.
    model = os.environ.get("OPENROUTER_DEFAULT_MODEL_FLASH")
    if not model:
        sys.exit("OPENROUTER_DEFAULT_MODEL_FLASH is not set; load the parent .env (see the plan)")
    return OpenRouterJudge(api_key=api_key, base_url=base_url, model=model)


def _refuse_a_migrating_dry_run(db_path: str) -> None:
    """A dry run that would migrate the store is not a dry run.

    Every store constructor calls `apply_migrations`, which stamps and commits
    DDL on anything behind `SCHEMA_VERSION` — so on an older store the promise
    at the top of this file ("nothing is written") would be false before the
    first judgement is asked. Read the version first, read-only, and say so.
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        sys.exit(
            f"{db_path} is at schema version {version}; this code expects "
            f"{SCHEMA_VERSION}, and opening it would migrate it — so a dry run "
            f"here would write. Run with --apply (it backs the store up first), "
            f"or migrate the store deliberately."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", help="the memory store to read (and, with --apply, write)")
    parser.add_argument("--apply", action="store_true", help="write the links; backs the store up first")
    args = parser.parse_args()
    load_dotenv()
    # Before anything opens the store, and before the backup: a missing pin
    # should not leave a stray .bak behind.
    judge = _judge()
    if args.apply:
        backup = f"{args.db}.bak-{date.today().isoformat()}-pre-relink"
        shutil.copy(args.db, backup)
        print(f"backup: {backup}")
    else:
        _refuse_a_migrating_dry_run(args.db)
    reports = asyncio.run(relink(args.db, judge, apply=args.apply))
    for report in reports:
        print(f"{report.uid[:8]}  {report.action:<14} {report.name}")
        for outcome in report.outcomes:
            if outcome.uid is None:
                print(f"          {outcome.name!r} unresolved after "
                      f"{outcome.attempts} attempt(s): {outcome.error}")
            else:
                print(f"          {outcome.name!r} -> {outcome.uid}"
                      + (f" ({outcome.attempts} attempts)" if outcome.attempts > 1 else ""))
    linked = sum(1 for r in reports if r.action in ("linked", "would link"))
    print(f"\n{len(reports)} unanchored durable rules: {linked} {'linked' if args.apply else 'would link'}, "
          f"{sum(1 for r in reports if r.action == 'no names')} with no names, "
          f"{sum(1 for r in reports if r.action == 'unresolved')} unresolved")
    partial = [r for r in reports if r.resolved and r.unresolved_names]
    print(f"{sum(len(r.unresolved_names) for r in reports)} names did not resolve, "
          f"across {len(partial)} partially linked rule(s) and "
          f"{sum(1 for r in reports if r.action == 'unresolved')} fully unresolved")


if __name__ == "__main__":
    main()
