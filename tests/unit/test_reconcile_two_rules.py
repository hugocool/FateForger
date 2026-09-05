"""Two rules, one tick, one prefix each: neither deletes the other's jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fateforger.haunt.reconcile import (
    DesiredJob,
    JobKey,
    PlanningReconciler,
    PlanningReminder,
    RequiredBlockOutcome,
)

from .test_reconcile import DummyCalendarClient, FakeScheduler

NOW = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


class _Rule:
    def __init__(self, rule_id: str, kinds: list[str]):
        self.rule_id, self._kinds, self.calls = rule_id, kinds, 0

    async def evaluate(self, *, now, scope, **kwargs):
        self.calls += 1
        return [
            DesiredJob(
                key=JobKey("rule", self.rule_id, scope, "2026-09-07", kind),
                run_at=now + timedelta(minutes=10),
                payload=PlanningReminder(scope=scope, kind=kind, attempt=1, message="m", user_id=scope),
            )
            for kind in self._kinds
        ]


class _RequiredRule:
    """The required-block contract: jobs plus the prefixes it could not judge."""

    rule_id = "required_blocks"

    def __init__(self, windows: list[str], undecided: list[str] | None = None):
        self._windows, self._undecided, self.calls = windows, list(undecided or []), 0

    async def evaluate(self, *, now, scope, **kwargs):
        self.calls += 1
        return RequiredBlockOutcome(
            jobs=[
                DesiredJob(
                    key=JobKey("rule", self.rule_id, scope, window, "nudge1"),
                    run_at=now + timedelta(minutes=10),
                    payload=PlanningReminder(scope=scope, kind="nudge1", attempt=1, message="m", user_id=scope),
                )
                for window in self._windows
            ],
            undecided=list(self._undecided),
        )


class _Explodes(_Rule):
    async def evaluate(self, **kwargs):
        raise RuntimeError("store down")


async def _noop(reminder):
    return None


@pytest.mark.asyncio
async def test_both_rules_run_and_each_keeps_its_own_jobs():
    scheduler = FakeScheduler()
    planning, required = _Rule("next_planning_session", ["nudge1"]), _RequiredRule(["2026-09-07"])
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=planning, required_block_rule=required)
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    ids = {job.id for job in scheduler.get_jobs()}
    assert ids == {"rule:next_planning_session:U1:2026-09-07:nudge1", "rule:required_blocks:U1:2026-09-07:nudge1"}
    assert (planning.calls, required.calls) == (1, 1)

    # the required block comes back: only its job goes
    required._windows = []
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {"rule:next_planning_session:U1:2026-09-07:nudge1"}


@pytest.mark.asyncio
async def test_a_failing_required_block_rule_does_not_stop_the_planning_ladder(caplog):
    scheduler = FakeScheduler()
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=_Rule("next_planning_session", ["nudge1"]),
                                    required_block_rule=_Explodes("required_blocks", []))
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {"rule:next_planning_session:U1:2026-09-07:nudge1"}
    assert any("required_blocks" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_without_a_required_block_rule_nothing_changes():
    scheduler = FakeScheduler()
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=_Rule("next_planning_session", ["nudge1"]))
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {"rule:next_planning_session:U1:2026-09-07:nudge1"}


def test_the_runtime_wiring_constructs_the_rule_with_the_calendar_and_timezone():
    from fateforger.haunt.required_block_rule import RequiredBlockConfig, RequiredBlockRule
    rule = RequiredBlockRule(calendar_client=object(), constraint_store=object(), ledger=object(),
                             config=RequiredBlockConfig(calendar_id="hugo@example.com", tz="Europe/Amsterdam"))
    assert rule.rule_id == "required_blocks"


@pytest.mark.asyncio
async def test_an_undecided_prefix_keeps_the_job_a_later_present_verdict_prunes():
    """R1: a tick that could not read leaves the ladder exactly as it was; the
    next tick that actually judges the block present is what clears it."""
    scheduler = FakeScheduler()
    required = _RequiredRule(["2026-09-07:planning"])
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=_Rule("next_planning_session", []), required_block_rule=required)
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    haunt_id = "rule:required_blocks:U1:2026-09-07:planning:nudge1"
    assert {job.id for job in scheduler.get_jobs()} == {haunt_id}

    # the calendar could not be read: no verdict, nothing pruned
    required._windows = []
    required._undecided = ["rule:required_blocks:U1:2026-09-07:planning:"]
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {haunt_id}

    # read succeeded and the block is back: now it goes
    required._undecided = []
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_a_rule_that_raises_prunes_nothing_of_its_own(caplog):
    """An exception is a failed read by another name: no verdict, no pruning."""
    scheduler = FakeScheduler()
    required = _RequiredRule(["2026-09-07:planning"])
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=_Rule("next_planning_session", []), required_block_rule=required)
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    haunt_id = "rule:required_blocks:U1:2026-09-07:planning:nudge1"

    reconciler._required_block_rule = _Explodes("required_blocks", [])
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {haunt_id}
