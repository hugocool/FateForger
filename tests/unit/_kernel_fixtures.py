"""Shared kernel test fixtures for skeleton-provenance and question tests.

`RowsContextPort`, `_skeleton_citing` and `_ROWS` started in
`test_adaptive_timeboxing_skeleton_provenance.py` (Task 2). Task 3 needs the
same day-with-one-known-rule fixture to build a skeleton artifact a
non-blocking question can ride with, so both test modules import from here
rather than keeping two copies that can drift apart.
"""

from __future__ import annotations

from fateforger.agents.timeboxing.adaptive_timeboxing import PlanningContext
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    PlanningResult,
)
from tests.unit.test_adaptive_timeboxing import RecordedContextPort


class RowsContextPort(RecordedContextPort):
    """A context port whose resolve returns real constraint rows.

    `RecordedContextPort` returns `{"items": []}` -- not a list -- so the
    kernel's `isinstance(rows, list)` branch never fires and the snapshot
    carries no rules. Provenance cannot be verified against nothing.
    """

    def __init__(self, rows: list[dict[str, object]]) -> None:
        super().__init__()
        self.rows = rows

    async def resolve(self, snapshot, *, target, progress):
        await super().resolve(snapshot, target=target, progress=progress)
        return PlanningContext(
            facts=list(self.facts),
            applicable_constraints=self.rows,
            calendar_snapshot={"events": []},
        )


def _skeleton_citing(rule_uid: str) -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.SKELETON,
                payload={
                    "day_label": "Saturday 5 September",
                    "groups": [
                        {"name": "Evening", "items": [
                            {"text": "Asleep by 23:00", "source": "rule",
                             "rule_uid": rule_uid},
                        ]},
                    ],
                    "reasoning": "",
                },
                dependency_revisions={"planning_day": 1},
            )
        ],
    )


_ROWS = [{"uid": "a1", "name": "Sleep schedule"}]


__all__ = ["RowsContextPort", "_skeleton_citing", "_ROWS"]
