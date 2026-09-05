"""Link the durable rules that predate the anchor graph to the anchors their
observations already name (#290).

`reproject` cannot do this: anchors are written to the append-only log at
ingest and re-projection re-asks projection only (I2). The split path already
relinks from observation anchor names; this is the same three calls as a
one-off pass. Resolving a name to an anchor is a judgement, so it goes to the
judge even on a dry run; nothing is minted (`max_new=0`): a name the judge
cannot place on an existing anchor is reported, not created.

Run on a copy first, then on the live store; `--apply` backs the store up
before writing. Links only: no observation, no projection, no other field.

    PYTHONPATH=src python scripts/memory/relink_anchors.py /path/copy.db
    PYTHONPATH=src python scripts/memory/relink_anchors.py data/memory.db --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint_store import ConstraintStore
from memory.judge import Judge
from memory.openrouter_judge import OpenRouterJudge
from memory.store import ObservationStore


@dataclass
class RelinkReport:
    uid: str
    name: str
    names: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    #: "linked" (or "would link" on a dry run), "no names" for a rule whose
    #: observations name no anchor, "unresolved" when the judge would mint
    action: str = ""


async def relink(db_path: str, judge: Judge, *, apply: bool) -> list[RelinkReport]:
    observations = ObservationStore(db_path)
    constraints = ConstraintStore(db_path)
    anchors = AnchorStore(db_path)
    reports: list[RelinkReport] = []
    for constraint in constraints.durable():
        if anchors.anchors_for(constraint.uid):
            continue
        report = RelinkReport(uid=constraint.uid, name=constraint.name)
        names: list[str] = []
        for observation_uid in constraints.observations_for(constraint.uid):
            observation = observations.get(observation_uid)
            if observation is not None:
                names.extend(observation.anchors)
        report.names = list(dict.fromkeys(names))
        if not report.names:
            report.action = "no names"
            reports.append(report)
            continue
        try:
            report.resolved = await resolve_anchors(report.names, anchors, judge, max_new=0)
        except ValueError as exc:
            report.action = f"unresolved: {exc}"
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", help="the memory store to read (and, with --apply, write)")
    parser.add_argument("--apply", action="store_true", help="write the links; backs the store up first")
    args = parser.parse_args()
    load_dotenv()
    if args.apply:
        backup = f"{args.db}.bak-{date.today().isoformat()}-pre-relink"
        shutil.copy(args.db, backup)
        print(f"backup: {backup}")
    reports = asyncio.run(relink(args.db, _judge(), apply=args.apply))
    for report in reports:
        print(f"{report.uid[:8]}  {report.action:<14} {report.name}")
        if report.names:
            print(f"          names: {report.names}")
            print(f"          anchors: {report.resolved}")
    linked = sum(1 for r in reports if r.action in ("linked", "would link"))
    print(f"\n{len(reports)} unanchored durable rules: {linked} {'linked' if args.apply else 'would link'}, "
          f"{sum(1 for r in reports if r.action == 'no names')} with no names, "
          f"{sum(1 for r in reports if r.action.startswith('unresolved'))} unresolved")


if __name__ == "__main__":
    main()
