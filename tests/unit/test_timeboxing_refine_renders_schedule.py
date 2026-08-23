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
