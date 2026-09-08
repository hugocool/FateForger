"""The reconcile tick has a cadence (R5).

`schedule_daily` runs the reconciler once a day. The watcher's whole job is to
notice a block that left the plan, and once a day is not noticing: the spec's
end-to-end case says "drag it to tomorrow, see the haunt on the next tick".
This is that tick.
"""
from __future__ import annotations

import pytest

from fateforger.core import runtime as runtime_module
from fateforger.haunt.planning_guardian import PlanningGuardian


class _RecordingScheduler:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def add_job(self, func, trigger, id, **kwargs):  # noqa: A002 - APScheduler's name
        self.added.append({"func": func, "trigger": trigger, "id": id, **kwargs})


def _guardian(scheduler) -> PlanningGuardian:
    return PlanningGuardian(scheduler, anchor_store=object(), reconciler=object())  # type: ignore[arg-type]


def test_the_interval_job_reconciles_every_fifteen_minutes():
    scheduler = _RecordingScheduler()
    guardian = _guardian(scheduler)

    guardian.schedule_interval()

    assert len(scheduler.added) == 1
    job = scheduler.added[0]
    assert job["id"] == "planning_guardian:interval_reconcile"
    assert job["trigger"] == "interval"
    assert job["minutes"] == 15
    assert job["func"] == guardian.reconcile_all
    # A tick that overran must not stack behind itself, and a run that was
    # missed while the process was busy is one run, not a queue of them.
    assert job["replace_existing"] is True
    assert job["coalesce"] is True
    assert job["max_instances"] == 1


def test_a_custom_interval_is_honoured():
    scheduler = _RecordingScheduler()
    _guardian(scheduler).schedule_interval(minutes=5)
    assert scheduler.added[0]["minutes"] == 5


@pytest.mark.parametrize("minutes", [0, -1])
def test_a_non_positive_interval_schedules_nothing(minutes):
    scheduler = _RecordingScheduler()
    _guardian(scheduler).schedule_interval(minutes=minutes)
    assert scheduler.added == []


def test_the_interval_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("FF_RECONCILE_INTERVAL_MINUTES", raising=False)
    assert runtime_module._reconcile_interval_minutes() == 15

    monkeypatch.setenv("FF_RECONCILE_INTERVAL_MINUTES", "5")
    assert runtime_module._reconcile_interval_minutes() == 5

    monkeypatch.setenv("FF_RECONCILE_INTERVAL_MINUTES", "0")
    assert runtime_module._reconcile_interval_minutes() == 0


def test_an_unreadable_interval_is_loud(monkeypatch):
    """A typo must not silently turn the watcher's cadence back to daily."""
    monkeypatch.setenv("FF_RECONCILE_INTERVAL_MINUTES", "every 15 minutes")
    with pytest.raises(RuntimeError):
        runtime_module._reconcile_interval_minutes()


def test_the_runtime_starts_the_interval_beside_the_daily_job():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(runtime_module))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "planning_guardian"
    }
    assert {"schedule_daily", "schedule_interval"} <= called
