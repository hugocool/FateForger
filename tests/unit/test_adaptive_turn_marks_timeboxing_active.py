"""The nudge suppressor was wired to the agent that no longer runs.

`dispatch_planning_reminder` already refuses to nudge while a timeboxing
session is in progress:

    if timeboxing_activity.is_active(reminder.user_id):
        ... "timeboxing active for %s; skipping"

But every `mark_active` call in the tree is in `agents/timeboxing/agent.py` --
the legacy `TimeboxingFlowAgent`, which Slack only reaches when
`FF_TIMEBOX_BACKEND=legacy`, and nothing sets that. The adaptive kernel that
actually serves every turn never marked anyone active, so `is_active` was
permanently False and the guard could not fire.

Measured on 2026-08-31: 12 user messages produced 12 reconciles that nudged and
12 identical "No planning session on the calendar yet" cards, several four
seconds apart, while the user was mid-session doing exactly the planning the
card was asking for.
"""

from fateforger.haunt.timeboxing_activity import timeboxing_activity


async def test_the_live_turn_marks_the_user_active(monkeypatch) -> None:
    """Driven through the real entry point, because the bug was that a
    perfectly good tracker was never called from this path."""

    import logging

    import fateforger.slack_bot.handlers as handlers
    from fateforger.agents.timeboxing.session_contracts import (
        PlanningSessionSnapshot,
        TurnFailed,
    )

    seen: list[bool] = []

    class Kernel:
        async def turn(self, request, progress):
            # Captured *during* the turn: that is when a nudge would land.
            seen.append(timeboxing_activity.is_active("U1"))
            return TurnFailed(code="x", message="x")

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return PlanningSessionSnapshot(
                session_key=key, revision=1, owner_user_id=owner_user_id
            )

    class Runtime:
        timeboxing_session_store = Repo()

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent",
                        lambda *a, **k: _noop_intent())
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "render_outcome", lambda *a, **k: "rendered")
    monkeypatch.setattr(handlers, "present_outcome", lambda *a, **k: ("rendered", None))

    timeboxing_activity.mark_inactive(user_id="U1")
    await handlers._run_adaptive_timebox_turn(
        runtime=Runtime(), client=object(), logger=logging.getLogger(__name__),
        session_key="C1:1.0", actor_user_id="U1", interaction_id="1.1",
        progress_channel="C1", progress_ts="1.0",
        card_channel="C1", card_thread_ts="1.0", user_text="plan my day",
    )
    timeboxing_activity.mark_inactive(user_id="U1")

    assert seen == [True], "the turn must mark the user active before it runs"


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


async def _noop_intent():
    from fateforger.agents.timeboxing.session_contracts import Advance

    return Advance()


async def test_marking_active_suppresses_the_nudge() -> None:
    """The whole point: the guard in dispatch_planning_reminder can now fire."""

    timeboxing_activity.mark_inactive(user_id="U1")
    assert timeboxing_activity.is_active("U1") is False
    timeboxing_activity.mark_active(user_id="U1", channel_id="C1", thread_ts="1.0")
    assert timeboxing_activity.is_active("U1") is True
    timeboxing_activity.mark_inactive(user_id="U1")
    assert timeboxing_activity.is_active("U1") is False


async def test_a_finished_session_stops_suppressing(monkeypatch) -> None:
    """Over-suppression is the opposite failure and just as quiet.

    Left marked active, a committed session would keep the Admonisher silent
    for the tracker's whole idle timeout -- so a user who finished planning at
    09:00 would get no nudge about the session they never booked.
    """

    import logging

    import fateforger.slack_bot.handlers as handlers
    from fateforger.agents.timeboxing.session_contracts import (
        PlanningSessionSnapshot,
        TurnFailed,
    )

    class Kernel:
        async def turn(self, request, progress):
            return TurnFailed(code="x", message="x")

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return PlanningSessionSnapshot(
                session_key=key, revision=1, owner_user_id=owner_user_id,
                status="committed",
            )

    class Runtime:
        timeboxing_session_store = Repo()

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", lambda *a, **k: _noop_intent())
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "render_outcome", lambda *a, **k: "rendered")
    monkeypatch.setattr(handlers, "present_outcome", lambda *a, **k: ("rendered", None))

    await handlers._run_adaptive_timebox_turn(
        runtime=Runtime(), client=object(), logger=logging.getLogger(__name__),
        session_key="C1:2.0", actor_user_id="U2", interaction_id="2.1",
        progress_channel="C1", progress_ts="2.0",
        card_channel="C1", card_thread_ts="2.0", user_text="yes commit it",
    )

    assert timeboxing_activity.is_active("U2") is False
