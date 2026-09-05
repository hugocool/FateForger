"""The shared Stage 1 fixture (#283): three locked days, one request.

Every spike runs exactly this. The store is frozen: FIXTURE_STORE_SHA256 pins
its bytes and rows_for refuses any other, so an eval never silently measures
against a moved corpus. Ground truth is constructed by ablation
(tests/evals/test_stage1_elicitation.py), not hand-labelled.
"""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)

#: sha256 of data/fixtures/stage1-20260905.db, the post-relink copy frozen on
#: 2026-09-05. Re-freezing is deliberate: bump this and re-run the evals.
#: These are the bytes before and after a KGConstraintMemoryClient open -- the
#: schema ladder is a no-op on this store, measured on the frozen copy.
FIXTURE_STORE_SHA256 = "e99dc318b1d73925be8f059a1e296c1f58c9fa84fe3f27279eb60a5870728699"


def verify_store(db_path: str) -> None:
    digest = hashlib.sha256(Path(db_path).read_bytes()).hexdigest()
    if digest != FIXTURE_STORE_SHA256:
        raise RuntimeError(
            f"fixture store sha256 {digest[:12]}… does not match the pinned "
            f"{FIXTURE_STORE_SHA256[:12]}…; the evals measure against one frozen store"
        )


@dataclass(frozen=True, slots=True)
class FixtureDay:
    key: str
    date: date
    day_type: DayType
    request: str


REQUEST = "deep work in the morning, gym at 18:00"

FIXTURE_DAYS: tuple[FixtureDay, ...] = (
    FixtureDay("working_tuesday", date(2026, 9, 8), DayType.WORKING, REQUEST),
    FixtureDay("vacation_day", date(2026, 9, 9), DayType.VACATION, REQUEST),
    FixtureDay("sunday", date(2026, 9, 13), DayType.WEEKEND, REQUEST),
)


def snapshot_for(day: FixtureDay, rows: list[dict]) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key=f"fixture:{day.key}",
        revision=1,
        owner_user_id="U_FIXTURE",
        planning_day=PlanningDay.lock_default(
            value=day.date, timezone="Europe/Amsterdam", lock_revision=1, day_type=day.day_type
        ),
        facts=[
            PlanningFact(
                fact_id="request-1", kind=FactKind.REQUESTED_ACTIVITY, value=day.request, source="user"
            )
        ],
        applicable_constraints=rows,
    )


def rows_for(db_path: str, day: FixtureDay) -> list[dict]:
    """The rows the KG client would hand the host for this day, from a copy."""
    verify_store(db_path)

    from fateforger.agents.timeboxing.kg_constraint_client import KGConstraintMemoryClient
    import asyncio

    client = KGConstraintMemoryClient(db_path)
    return asyncio.run(
        client.query_constraints(
            filters={"planned_day": day.date.isoformat(), "day_type": day.day_type.value}, limit=200
        )
    )


GOLDEN = Path(__file__).resolve().parent / "golden.toml"


def load_golden(path: Path = GOLDEN) -> dict[str, list[str]]:
    """Per fixture day, the facts the simulated user may answer from. A day
    with no golden facts fails loudly: a simulated user with nothing to say
    would make every probe a nuisance and the measure meaningless."""
    raw = tomllib.loads(path.read_text())
    golden = {key: [str(line) for line in section.get("facts", [])] for key, section in raw.items()}
    for day in FIXTURE_DAYS:
        if not golden.get(day.key):
            raise ValueError(f"{day.key} has no golden facts in {path}")
    return golden
