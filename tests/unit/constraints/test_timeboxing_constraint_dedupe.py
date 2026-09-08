from __future__ import annotations

from datetime import datetime, timezone

from fateforger.agents.timeboxing.agent import _dedupe_constraints
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)


def _constraint(
    *,
    name: str,
    description: str,
    status: ConstraintStatus,
    updated_at: datetime,
    uid: str | None = None,
) -> Constraint:
    hints = {"rule_kind": "commute"}
    if uid:
        hints["uid"] = uid
    return Constraint(
        user_id="U1",
        channel_id=None,
        thread_ts=None,
        name=name,
        description=description,
        necessity=ConstraintNecessity.MUST,
        status=status,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        tags=["commute"],
        hints=hints,
        start_date=None,
        end_date=None,
        days_of_week=[],
        timezone="Europe/Amsterdam",
        updated_at=updated_at,
    )


def test_dedupe_constraints_prefers_locked_over_proposed_for_same_semantics() -> None:
    proposed = _constraint(
        name="Commute Home",
        description="Commute office to home",
        status=ConstraintStatus.PROPOSED,
        updated_at=datetime(2026, 3, 6, 8, 0, tzinfo=timezone.utc),
    )
    locked = _constraint(
        name="Commute Home",
        description="Commute office to home",
        status=ConstraintStatus.LOCKED,
        updated_at=datetime(2026, 3, 6, 7, 0, tzinfo=timezone.utc),
    )

    deduped = _dedupe_constraints([proposed, locked])

    assert len(deduped) == 1
    assert deduped[0].status == ConstraintStatus.LOCKED


def test_dedupe_constraints_keeps_distinct_uids_even_with_same_text() -> None:
    first = _constraint(
        name="Morning Routine",
        description="Normal morning routine",
        status=ConstraintStatus.LOCKED,
        updated_at=datetime(2026, 3, 6, 7, 0, tzinfo=timezone.utc),
        uid="tb:pref:1",
    )
    second = _constraint(
        name="Morning Routine",
        description="Normal morning routine",
        status=ConstraintStatus.LOCKED,
        updated_at=datetime(2026, 3, 6, 8, 0, tzinfo=timezone.utc),
        uid="tb:pref:2",
    )

    deduped = _dedupe_constraints([first, second])

    assert len(deduped) == 2
