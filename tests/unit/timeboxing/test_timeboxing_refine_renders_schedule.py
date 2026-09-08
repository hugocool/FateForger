"""Stage 4 must show the user the schedule it just changed.

Reconstructed from a real session (2026-03-08, thread 1772956522.019509).
The patcher succeeded on all five Refine passes and the plan grew from 6 to 13
events, yet every rendered message carried only a prose paragraph. The first
message was 393 characters -- complete, not truncated -- and read:

    ### Draft Overview
    The draft accommodates your early F1 race, a morning deep work block, ...
    ### What I need from you
    Review the refined schedule above.

There was no schedule above. Twenty-four seconds later the user replied
"Commit, but you don't show the schedule", and eight minutes later, after the
patcher had added a deep work block and an evening routine they had asked for,
"You didn't add anything."

The plan was changing. Only the rendering never said so.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import (
    FreeformSection,
    SessionMessage,
    StageGateOutput,
    TimeboxingStage,
)
from fateforger.agents.timeboxing.timebox import Timebox
from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType


def _prose_only_gate() -> StageGateOutput:
    """The gate as Stage 4 actually produced it: overview prose, no schedule."""
    return StageGateOutput(
        stage_id=TimeboxingStage.REFINE,
        ready=True,
        summary=[],
        missing=[],
        question="Review the refined schedule above.",
        facts={},
        response_message=SessionMessage(
            sections=[
                FreeformSection(
                    heading="Draft Overview",
                    content=(
                        "The draft accommodates your early F1 race, a morning "
                        "deep work block, and perfectly buffers your afternoon "
                        "hockey game with commute times."
                    ),
                ),
            ]
        ),
    )


def _timebox() -> Timebox:
    """Two blocks the user asked for and the patcher really did add."""
    return Timebox(
        events=[
            CalendarEvent(
                summary="Deep Work: Secondary Lane",
                event_type=EventType.DEEP_WORK,
                start_time=time(16, 30),
                duration=timedelta(hours=1, minutes=30),
            ),
            CalendarEvent(
                summary="Evening Wind-Down",
                event_type=EventType.REGENERATION,
                start_time=time(21, 0),
                duration=timedelta(hours=2),
            ),
        ],
        date=date(2026, 3, 8),
        timezone="Europe/Amsterdam",
    )


def test_refine_message_names_the_blocks_in_the_plan() -> None:
    """A Refine message that cannot name the user's blocks has not shown them."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    message = agent._format_stage_message(
        _prose_only_gate(),
        constraints=[],
        immovables=[],
        timebox=_timebox(),
    )

    assert "Deep Work: Secondary Lane" in message
    assert "Evening Wind-Down" in message


def test_refine_message_gives_each_block_a_time() -> None:
    """Names alone are not a schedule -- the user asked when, twice."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    message = agent._format_stage_message(
        _prose_only_gate(),
        constraints=[],
        immovables=[],
        timebox=_timebox(),
    )

    assert "16:30" in message
    assert "21:00" in message


def test_the_plan_is_rendered_even_when_the_model_wrote_its_own_schedule() -> None:
    """The render is unconditional, not "only if the model forgot".

    The two tests above use a prose-only gate, so they prove that a gate with
    no schedule gets one. They do not prove the invariant, and the difference
    is not academic: this mutation passes both of them --

        _already = any("schedule" in (sec.heading or "").lower()
                       for sec in gate.response_message.sections)
        schedule_lines = [] if _already else self._format_schedule_lines(timebox)

    "Don't duplicate what the model already wrote" is a plausible,
    well-intentioned refactor. It also reinstates exactly the failure this file
    exists for, because the incident *was* a stage model writing something that
    read like a schedule. Whether its prose really showed the plan is a
    judgement about generated text, and it was wrong five times out of five.

    So: a gate that already carries a "Schedule" section, holding a stale plan
    that shares not one block with the real one. The authoritative render comes
    from `session.timebox` regardless, and the blocks that actually exist are
    the ones the user sees.

    Found by admonish-1-c5, who mutated the invariant rather than the renderer.
    """
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.REFINE,
        ready=True,
        summary=[],
        missing=[],
        question="Review the refined schedule above.",
        facts={},
        response_message=SessionMessage(
            sections=[
                FreeformSection(
                    heading="Schedule",
                    content="- 09:00-10:00 Yesterday's Standup\n- 10:00-11:00 Inbox",
                ),
            ]
        ),
    )

    message = agent._format_stage_message(
        gate, constraints=[], immovables=[], timebox=_timebox()
    )

    assert "Deep Work: Secondary Lane" in message
    assert "Evening Wind-Down" in message
    assert "16:30" in message
