"""The shared Stage 1 fixture (#283): three locked days, one request.

Every spike runs exactly this. The store is never the live one: `rows_for`
takes a path the caller copied into a temp dir. Hand labels live in
labels.toml beside this file; a day with no labels fails loudly, because a
spike measured against no ground truth measures the model's opinion of itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import tomllib

from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
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
    from fateforger.agents.timeboxing.kg_constraint_client import KGConstraintMemoryClient
    import asyncio

    client = KGConstraintMemoryClient(db_path)
    return asyncio.run(
        client.query_constraints(
            filters={"planned_day": day.date.isoformat(), "day_type": day.day_type.value}, limit=200
        )
    )


@dataclass(frozen=True, slots=True)
class LabelledGap:
    cell: str
    hard: bool
    note: str


def load_labels(path: Path) -> dict[str, list[LabelledGap]]:
    raw = tomllib.loads(path.read_text())
    labels: dict[str, list[LabelledGap]] = {}
    for day_key, section in raw.items():
        labels[day_key] = [
            LabelledGap(cell=str(g["cell"]), hard=bool(g.get("hard", False)), note=str(g.get("note", "")))
            for g in section.get("gaps", [])
        ]
    return labels
