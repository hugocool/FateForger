"""Unit tests for PlannerAgent's MCP workbench reset-and-retry-once protection.

Regression coverage for the 2026-09-09 incident: the google-calendar-mcp
container crash-looped, and PlannerAgent cached one McpWorkbench for the
process lifetime with no health check and no reset. AutoGen's
McpWorkbench.call_tool only calls start() when its actor is falsy, so once an
actor existed with a dead session, every later call raised "MCP Actor not
running, call initialize() first" forever -- even after the server recovered,
and even on "Try again". Only a bot restart fixed it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.schedular.agent import PlannerAgent
from fateforger.agents.schedular.messages import SuggestNextSlot, UpsertCalendarEvent

_ACTOR_DEAD = "MCP Actor not running, call initialize() first"


class _DummyHaunt:
    def register_agent(self, *args, **kwargs):
        return None

    async def record_envelope(self, *args, **kwargs):
        return None


class _RecoverThenSucceedWorkbench:
    """Fails once with a given message, then returns a fixed result forever."""

    def __init__(self, *, fail_message: str, ok_result: object) -> None:
        self._fail_message = fail_message
        self._ok_result = ok_result
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict) -> object:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(self._fail_message)
        return self._ok_result


class _AlwaysFailWorkbench:
    def __init__(self, message: str) -> None:
        self._message = message
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict) -> object:
        self.calls += 1
        raise RuntimeError(self._message)


def _planner_with_reset_tracking(workbench: object) -> tuple[PlannerAgent, dict]:
    """Build a PlannerAgent whose reset is observable but keeps returning
    the same fake workbench (so the fake's own call-counting state survives
    a "reset")."""
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    agent._workbench = workbench
    resets = {"count": 0}

    async def _tracking_reset() -> None:
        resets["count"] += 1
        # A real reset drops the dead workbench; simulate that but keep
        # handing back the same fake object so its call/attempt counters
        # remain meaningful to the assertions below.
        agent._workbench = workbench

    agent._reset_workbench = _tracking_reset  # type: ignore[method-assign]
    return agent, resets


# ---------------------------------------------------------------------------
# _call_tool_with_retry, tested directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recoverable_error_resets_once_and_returns_the_retry_result() -> None:
    workbench = _RecoverThenSucceedWorkbench(
        fail_message=_ACTOR_DEAD, ok_result="second-attempt-result"
    )
    agent, resets = _planner_with_reset_tracking(workbench)

    result = await agent._call_tool_with_retry(
        "list-events", {"calendarId": "primary"}, retry=True
    )

    assert result == "second-attempt-result"
    assert workbench.calls == 2
    assert resets["count"] == 1


@pytest.mark.asyncio
async def test_non_recoverable_error_propagates_with_no_reset_and_no_retry() -> None:
    workbench = _AlwaysFailWorkbench("Invalid arguments for tool get-event")
    agent, resets = _planner_with_reset_tracking(workbench)

    with pytest.raises(RuntimeError, match="Invalid arguments"):
        await agent._call_tool_with_retry("get-event", {"eventId": "x"}, retry=True)

    assert workbench.calls == 1
    assert resets["count"] == 0


@pytest.mark.asyncio
async def test_retry_budget_is_one_not_unbounded() -> None:
    """Two consecutive recoverable errors must raise, not loop forever."""
    workbench = _AlwaysFailWorkbench(_ACTOR_DEAD)
    agent, resets = _planner_with_reset_tracking(workbench)

    with pytest.raises(RuntimeError, match="MCP Actor not running"):
        await agent._call_tool_with_retry(
            "list-events", {"calendarId": "primary"}, retry=True
        )

    # Exactly the initial attempt plus one retry -- not an unbounded loop --
    # and exactly one reset, not one per failed attempt.
    assert workbench.calls == 2
    assert resets["count"] == 1


@pytest.mark.asyncio
async def test_retry_false_resets_the_workbench_but_never_resends_the_call() -> None:
    """Locks the write-safety decision for mutating calls (create-event).

    A recoverable error still means the dead workbench gets discarded, so
    the *next* user press has a working workbench -- but the failed call
    itself is never automatically resent. If someone later made creates
    retry blindly (retry=True), this test's call-count assertion fails.
    """
    workbench = _RecoverThenSucceedWorkbench(
        fail_message=_ACTOR_DEAD, ok_result="would-be-duplicate-event"
    )
    agent, resets = _planner_with_reset_tracking(workbench)

    with pytest.raises(RuntimeError, match="MCP Actor not running"):
        await agent._call_tool_with_retry(
            "create-event", {"summary": "x"}, retry=False
        )

    assert workbench.calls == 1  # never resent
    assert resets["count"] == 1  # but the dead workbench was still discarded


# ---------------------------------------------------------------------------
# Wiring: the real call sites actually route through the helper above.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_next_slot_recovers_from_one_recoverable_list_events_failure() -> None:
    workbench = _RecoverThenSucceedWorkbench(
        fail_message=_ACTOR_DEAD,
        ok_result={"events": [], "totalCount": 0},
    )
    agent, _ = _planner_with_reset_tracking(workbench)

    result = await agent.handle_suggest_next_slot(
        SuggestNextSlot(
            calendar_id="primary",
            duration_min=30,
            time_zone="Europe/Amsterdam",
            horizon_days=1,
            work_start_hour=0,
            work_end_hour=23,
        ),
        None,
    )

    assert result.ok is True
    assert workbench.calls == 2


@pytest.mark.asyncio
async def test_upsert_calendar_event_does_not_blindly_retry_a_create() -> None:
    """End-to-end lock: handle_upsert_calendar_event must not auto-resend
    create-event after a recoverable transport error, even though the retry
    would have "succeeded" here -- resending it for real would double-book
    Hugo's calendar if the first create actually reached the server."""

    class _Workbench:
        def __init__(self) -> None:
            self.create_calls = 0
            self.get_calls = 0

        async def call_tool(self, name: str, arguments: dict) -> object:
            if name == "get-event":
                self.get_calls += 1
                # No pre-existing event -> create path.
                raise RuntimeError("event not found")
            if name == "create-event":
                self.create_calls += 1
                raise RuntimeError(_ACTOR_DEAD)
            raise AssertionError(f"unexpected tool call: {name}")

    workbench = _Workbench()
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    agent._workbench = workbench

    result = await agent.handle_upsert_calendar_event(
        UpsertCalendarEvent(
            calendar_id="primary",
            event_id="evt-1",
            summary="Daily planning session",
            description="Plan tomorrow",
            start="2026-02-27T17:20:51.252588+00:00",
            end="2026-02-27T17:50:51.252588+00:00",
            time_zone="Europe/Amsterdam",
            color_id="10",
        ),
        None,
    )

    assert result.ok is False
    assert "MCP Actor not running" in (result.error or "")
    assert workbench.create_calls == 1  # never blindly retried
    # The dead workbench must have been discarded so the *next* user press
    # (a fresh handle_upsert_calendar_event call) gets a working one.
    assert agent._workbench is not workbench or agent._workbench is None
