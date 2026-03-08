from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.constraint_reconciliation import (
    reconcile_constraint_rows,
)


def _row(
    *,
    uid: str,
    status: str = "proposed",
    updated_at: str = "2026-03-06T09:00:00+00:00",
    start_date: str | None = None,
    end_date: str | None = None,
    days_of_week: list[str] | None = None,
    applies_stages: list[str] | None = None,
) -> dict[str, object]:
    return {
        "uid": uid,
        "name": "office_commute_home",
        "description": "Commute office to home",
        "necessity": "must",
        "status": status,
        "scope": "profile",
        "source": "user",
        "rule_kind": "commute",
        "topics": ["commute", "office"],
        "start_date": start_date,
        "end_date": end_date,
        "days_of_week": list(days_of_week or []),
        "applies_stages": list(applies_stages or []),
        "updated_at": updated_at,
    }


def test_reconcile_rows_keeps_strongest_canonical_candidate() -> None:
    rows = [
        _row(uid="legacy-1", status="proposed", updated_at="2026-03-05T09:00:00+00:00"),
        _row(uid="new-1", status="locked", updated_at="2026-03-06T09:00:00+00:00"),
    ]

    result = reconcile_constraint_rows(
        rows=rows,
        planned_day=date(2026, 3, 6),
        stage="refine",
    )

    assert result.raw_count == 2
    assert result.canonical_count == 1
    assert result.applicable_count == 1
    assert result.applicable_rows[0]["uid"] == "new-1"
    assert result.duplicate_groups == [
        {"canonical_uid": "new-1", "duplicate_uids": ["legacy-1"]}
    ]


def test_reconcile_rows_filters_out_non_applicable_day_and_stage() -> None:
    rows = [
        _row(
            uid="weekend-only",
            days_of_week=["SA", "SU"],
            applies_stages=["skeleton"],
        )
    ]

    result = reconcile_constraint_rows(
        rows=rows,
        planned_day=date(2026, 3, 6),  # Friday
        stage="refine",
    )

    assert result.raw_count == 1
    assert result.canonical_count == 1
    assert result.applicable_count == 0


def test_reconcile_rows_drops_declined_constraints() -> None:
    rows = [_row(uid="declined-1", status="declined")]

    result = reconcile_constraint_rows(
        rows=rows,
        planned_day=date(2026, 3, 6),
        stage="collect_constraints",
    )

    assert result.raw_count == 1
    assert result.canonical_count == 1
    assert result.applicable_count == 0
