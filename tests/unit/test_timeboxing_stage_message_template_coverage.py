"""Tests for Stage 1 constraint-template coverage rendering."""

from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage


def test_collect_constraints_message_omits_template_coverage_even_when_facts_present() -> None:
    """Stage 1 message should not include template-coverage sections in Slack output."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["Anchored brunch and dinner windows."],
        missing=["timezone"],
        question="What timezone should I use for this plan?",
        facts={
            "constraint_overview": {
                "durable_applies": ["No meetings before 10:00"],
                "day_specific_applies": ["Dinner prep starts at 18:00"],
            },
            "constraint_template": {
                "filled_fields": ["name", "description", "necessity", "scope"],
                "useful_next_fields": ["days_of_week", "timezone", "selector"],
            },
        },
    )

    message = agent._format_stage_message(gate, constraints=[], immovables=[])

    assert "Constraint Template Coverage:" not in message
    assert "Filled:" not in message
    assert "Durable Applies:" not in message
    assert "Day-Specific Applies:" not in message
    assert "Useful Next Info:" not in message


def test_collect_constraints_message_omits_template_section_when_facts_missing() -> None:
    """Stage 1 message should not post-process strings when template facts are missing."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["Anchored morning rhythm."],
        missing=["timezone", "days_of_week"],
        question="Any weekday/weekend differences I should account for?",
        facts={},
    )
    constraint = Constraint(
        name="Morning deep work",
        description="Protect morning deep-work window.",
        necessity=ConstraintNecessity.MUST,
        user_id="U1",
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        timezone="Europe/Amsterdam",
    )

    message = agent._format_stage_message(gate, constraints=[constraint], immovables=[])

    assert "Constraint Template Coverage:" not in message


def test_collect_constraints_message_deduplicates_only() -> None:
    """Stage 1 summary should preserve model wording and only deduplicate exact repeats."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=[
            "February 14th, 2026 is our canvas.",
            "Timezone set to Europe/Amsterdam.",
            "Anchored brunch at 11:30.",
            "Anchored brunch at 11:30.",
            "No fixed anchors or windows are currently defined.",
        ],
        missing=["work window"],
        question="Any hard appointments?",
        facts={},
    )

    message = agent._format_stage_message(gate, constraints=[], immovables=[])

    assert "February 14th, 2026 is our canvas." in message
    assert "Timezone set to Europe/Amsterdam." in message
    assert "No fixed anchors or windows are currently defined." in message
    assert message.count("Anchored brunch at 11:30.") == 1
