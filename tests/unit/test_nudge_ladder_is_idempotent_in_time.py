"""Re-running reconciliation must not restart the escalation ladder.

`PlanningSessionRule._resolve_nudge_offsets` produces a correct exponential
series -- 10, 20, 40, 80, 160 minutes, capped at 8 hours. It is then discarded,
because `reconcile.py` anchors every job to `now`:

    start = now.astimezone(timezone.utc)
    run_at = start + offset

and re-adds each one with `replace_existing=True`. So every reconcile pass
rewrites the whole ladder relative to the current moment: nudge1 is perpetually
re-armed at now+10, fires, and is re-armed again.

Measured live on 2026-08-31 -- Hugo received a card roughly every 10 minutes and
suspected a duplicate process. There was exactly one bot. The emitted sequence
was `nudge1, nudge1, nudge1, nudge1, nudge2, nudge1, nudge2, nudge3`: attempt 1
fired five times and the decay never took effect.

Identity was never the problem -- `JobKey` already includes the day, so keys are
stable per (rule, scope, day). Only the time moved.
"""

from datetime import datetime, timedelta, timezone

import pytest

from fateforger.haunt.reconcile import PlanningReconciler

from .test_reconcile import DummyCalendarClient, FakeScheduler


def _reconciler(scheduler):
    async def dispatch(reminder):
        return None

    return PlanningReconciler(
        scheduler, calendar_client=DummyCalendarClient(events=[]), dispatcher=dispatch
    )


def _run_times(scheduler):
    return {j.id: j.trigger.run_date for j in scheduler.get_jobs()}


@pytest.mark.asyncio
async def test_a_second_reconcile_does_not_move_the_ladder() -> None:
    """The assertion that fails today: same day, same scope, same times."""

    scheduler = FakeScheduler()
    reconciler = _reconciler(scheduler)
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="C1:1", user_id="U1", channel_id="C1", now=t0
    )
    first = _run_times(scheduler)

    await reconciler.reconcile_missing_planning(
        scope="C1:1", user_id="U1", channel_id="C1", now=t0 + timedelta(minutes=5)
    )
    second = _run_times(scheduler)

    assert first == second


@pytest.mark.asyncio
async def test_the_gaps_stay_exponential_across_reconciles() -> None:
    """What the user actually experiences: a decaying series, not a drip."""

    scheduler = FakeScheduler()
    reconciler = _reconciler(scheduler)
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)

    for minutes in (0, 3, 7, 11):
        await reconciler.reconcile_missing_planning(
            scope="C1:1", user_id="U1", channel_id="C1",
            now=t0 + timedelta(minutes=minutes),
        )

    nudges = sorted(
        (j.trigger.run_date for j in scheduler.get_jobs() if "nudge" in j.id)
    )
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(nudges, nudges[1:])]
    assert gaps == [10, 20, 40, 80]


@pytest.mark.asyncio
async def test_an_explicit_first_nudge_offset_still_re_arms() -> None:
    """`planning_guardian` re-times the first nudge on purpose; that must survive.

    Freezing every job would silently ignore a caller asking for a different
    schedule -- the opposite failure, and just as quiet.
    """

    scheduler = FakeScheduler()
    reconciler = _reconciler(scheduler)
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="C1:1", user_id="U1", channel_id="C1", now=t0
    )
    before = _run_times(scheduler)

    await reconciler.reconcile_missing_planning(
        scope="C1:1", user_id="U1", channel_id="C1",
        now=t0 + timedelta(minutes=5),
        first_nudge_offset=timedelta(minutes=45),
    )
    assert _run_times(scheduler) != before
