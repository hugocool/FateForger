"""Unit tests for calendar reconciliation and duplicate prevention."""

from __future__ import annotations

from datetime import date, time

from fateforger.agents.timeboxing.calendar_reconciliation import (
    reconcile_calendar_ops,
)
from fateforger.agents.timeboxing.tb_models import FixedWindow, TBEvent, TBPlan

PLAN_DATE = date(2026, 2, 14)
TZ = "Europe/Amsterdam"


def _fw(name: str, st: time, et: time, *, event_type: str = "DW") -> TBEvent:
    return TBEvent(n=name, d="", t=event_type, p=FixedWindow(st=st, et=et))


def test_owned_event_fuzzy_match_becomes_update_candidate() -> None:
    """Moved owned events should reconcile as update candidates, not creates."""
    remote = TBPlan(events=[_fw("Focus", time(9, 0), time(10, 0))], date=PLAN_DATE, tz=TZ)
    desired = TBPlan(events=[_fw("Focus", time(9, 15), time(10, 15))], date=PLAN_DATE, tz=TZ)

    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map={},
        remote_event_ids_by_index=["fftb-owned-1"],
    )

    assert len(plan.creates) == 0
    assert len(plan.updates) == 1
    assert plan.updates[0].match_kind == "fuzzy"
    assert plan.updates[0].remote.event_id == "fftb-owned-1"


def test_foreign_event_changes_are_noop_and_not_created() -> None:
    """Foreign events are matched but never mutated or duplicated."""
    remote = TBPlan(events=[_fw("Lunch", time(12, 0), time(13, 0), event_type="M")], date=PLAN_DATE, tz=TZ)
    desired = TBPlan(events=[_fw("Lunch", time(12, 10), time(13, 10), event_type="M")], date=PLAN_DATE, tz=TZ)

    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map={},
        remote_event_ids_by_index=["foreign-id-1"],
    )

    assert len(plan.creates) == 0
    assert len(plan.updates) == 0
    assert len(plan.noops) == 1
    assert plan.noops[0].remote.event_id == "foreign-id-1"


def test_foreign_overlap_with_different_summary_is_noop_and_not_created() -> None:
    """Near-identical foreign overlap should not produce a duplicate desired create."""
    remote = TBPlan(events=[_fw("Lunch", time(13, 0), time(14, 0), event_type="M")], date=PLAN_DATE, tz=TZ)
    desired = TBPlan(events=[_fw("Lunch Break", time(13, 0), time(14, 0), event_type="M")], date=PLAN_DATE, tz=TZ)

    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map={},
        remote_event_ids_by_index=["foreign-id-2"],
    )

    assert len(plan.creates) == 0
    assert len(plan.updates) == 0
    assert len(plan.noops) == 1
    assert plan.noops[0].remote.event_id == "foreign-id-2"


def test_unmatched_owned_remote_is_delete_candidate() -> None:
    """Owned remote events missing from desired should be deleted."""
    remote = TBPlan(events=[_fw("Old", time(8, 0), time(9, 0))], date=PLAN_DATE, tz=TZ)
    desired = TBPlan(events=[], date=PLAN_DATE, tz=TZ)

    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map={},
        remote_event_ids_by_index=["fftb-old-1"],
    )

    assert len(plan.deletes) == 1
    assert plan.deletes[0].event_id == "fftb-old-1"


def test_repeated_summaries_match_deterministically() -> None:
    """Time-adjacent duplicates should reconcile one-to-one in stable order."""
    remote = TBPlan(
        events=[
            _fw("Focus", time(9, 0), time(10, 0)),
            _fw("Focus", time(11, 0), time(12, 0)),
        ],
        date=PLAN_DATE,
        tz=TZ,
    )
    desired = TBPlan(
        events=[
            _fw("Focus", time(9, 10), time(10, 10)),
            _fw("Focus", time(11, 10), time(12, 10)),
        ],
        date=PLAN_DATE,
        tz=TZ,
    )

    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map={},
        remote_event_ids_by_index=["fftb-a", "fftb-b"],
    )

    assert len(plan.updates) == 2
    assert [match.remote.event_id for match in plan.updates] == ["fftb-a", "fftb-b"]
    assert len(plan.creates) == 0


def test_remote_overlapping_snapshot_does_not_crash_reconciliation() -> None:
    """Overlapping remote snapshots should reconcile instead of raising ValueError."""
    remote = TBPlan(
        events=[
            _fw("Lunch", time(13, 0), time(14, 0), event_type="R"),
            _fw("Deep Work: Facet extraction", time(13, 30), time(15, 0)),
        ],
        date=PLAN_DATE,
        tz=TZ,
    )
    desired = TBPlan(
        events=[
            _fw("Lunch", time(13, 0), time(13, 30), event_type="R"),
            _fw("Deep Work: Facet extraction", time(13, 30), time(15, 0)),
        ],
        date=PLAN_DATE,
        tz=TZ,
    )

    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map={"Deep Work: Facet extraction|13:30:00": "fftb-deep-1"},
        remote_event_ids_by_index=["foreign-lunch-1", "fftb-deep-1"],
    )

    assert len(plan.creates) == 0
    assert any(match.remote.event_id == "fftb-deep-1" for match in plan.updates + plan.noops)
