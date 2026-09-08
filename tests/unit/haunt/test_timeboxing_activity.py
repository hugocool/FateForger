import asyncio
from datetime import timedelta

import pytest

from fateforger.haunt.timeboxing_activity import TimeboxingActivityTracker


@pytest.mark.asyncio
async def test_marks_unfinished_after_idle_timeout():
    triggered = []

    async def on_idle(user_id: str) -> None:
        triggered.append(user_id)

    tracker = TimeboxingActivityTracker(idle_timeout=timedelta(milliseconds=10))
    tracker.set_on_idle(on_idle)
    tracker.mark_active(user_id="U1", channel_id="C1", thread_ts="T1")

    await asyncio.sleep(0.05)

    assert tracker.get_state("U1") == "unfinished"
    assert triggered == ["U1"]


@pytest.mark.asyncio
async def test_activity_refresh_resets_idle_timer():
    triggered = []

    async def on_idle(user_id: str) -> None:
        triggered.append(user_id)

    tracker = TimeboxingActivityTracker(idle_timeout=timedelta(milliseconds=20))
    tracker.set_on_idle(on_idle)
    tracker.mark_active(user_id="U1", channel_id="C1", thread_ts="T1")

    await asyncio.sleep(0.01)
    tracker.mark_active(user_id="U1", channel_id="C1", thread_ts="T1")

    await asyncio.sleep(0.015)
    assert tracker.is_active("U1")
    assert triggered == []

    await asyncio.sleep(0.03)
    assert tracker.get_state("U1") == "unfinished"
    assert triggered == ["U1"]
