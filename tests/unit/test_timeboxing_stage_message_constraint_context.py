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
from fateforger.agents.timeboxing.stage_gating import (
    ConstraintsSection,
    FreeformSection,
    NextStepsSection,
    SessionMessage,
    StageGateOutput,
    TimeboxingStage,
)


def _constraint(
    name: str,
    *,
    source: ConstraintSource = ConstraintSource.USER,
    status: ConstraintStatus = ConstraintStatus.LOCKED,
) -> Constraint:
    return Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name=name,
        description=f"{name} description",
        necessity=ConstraintNecessity.SHOULD,
        scope=ConstraintScope.PROFILE,
        source=source,
        status=status,
    )


def test_stage_message_shows_current_step_question_and_plain_constraint_details() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.REFINE,
        ready=True,
        summary=["Patched the morning block."],
        missing=[],
        question="Anything else to adjust?",
        facts={},
    )
    constraints = [_constraint(f"Constraint {idx}") for idx in range(1, 9)]

    message = agent._format_stage_message(gate=gate, constraints=constraints)

    assert "### Current step\nStage 4/5 (Refine)" in message
    assert "### What I need from you" in message
    assert "Anything else to adjust?" in message
    assert "### Constraints (top 3/8)" in message
    assert "### All active constraints" in message
    assert "<details>" not in message
    assert "</details>" not in message


def test_stage_message_marks_assumptions_as_yes_state_with_deny_edit_hint() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["Starting constraint collection."],
        missing=["sleep target"],
        question="What time do you want to sleep?",
        facts={},
    )
    constraints = [
        _constraint("Lock bedtime", source=ConstraintSource.USER, status=ConstraintStatus.LOCKED),
        _constraint(
            "Assume no calls before 10",
            source=ConstraintSource.SYSTEM,
            status=ConstraintStatus.PROPOSED,
        ),
    ]

    message = agent._format_stage_message(gate=gate, constraints=constraints)

    assert "### Current step\nStage 1/5 (CollectConstraints)" in message
    assert "### What I need from you" in message
    assert "What time do you want to sleep?" in message
    assert "### Assumptions currently applied (yes-state; deny/edit if wrong)" in message
    assert "needs confirmation; deny/edit if wrong" in message
    assert "### All active constraints" not in message


def test_stage_message_prefers_structured_section_payload_and_orders_sections() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.CAPTURE_INPUTS,
        ready=False,
        summary=["placeholder"],
        missing=["placeholder"],
        question="placeholder",
        facts={},
        response_message=SessionMessage(
            sections=[
                FreeformSection(heading="Context", content="Session context here."),
                ConstraintsSection(
                    content=["Protect 09:00-12:00 focus"],
                    folded_content=["Protect 09:00-12:00 focus", "No calls after 18:00"],
                ),
                NextStepsSection(content=["Confirm deep-work block count", "Reply with a number."]),
            ]
        ),
    )

    message = agent._format_stage_message(gate=gate, constraints=[], immovables=[])

    assert "### What I need from you" in message
    assert "### Constraints" in message
    assert "### Context" in message
    assert message.index("### Constraints") < message.index("### Context")
    assert message.index("### Context") < message.index("### What I need from you")


def test_stage_message_moves_freeform_next_steps_to_end_and_strips_disclosure_tags() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["placeholder"],
        missing=["placeholder"],
        question="placeholder",
        facts={},
        response_message=SessionMessage(
            sections=[
                FreeformSection(heading="Anchors", content="Day: Saturday, February 28, 2026"),
                FreeformSection(
                    heading="What I need from you",
                    content=(
                        "<details>\n"
                        "<summary>Show all constraints</summary>\n"
                        "Constraint searching: completed.\n"
                        "</details>\n"
                        "Confirm your wake-up time."
                    ),
                ),
                FreeformSection(heading="Greetings", content="Good afternoon."),
            ]
        ),
    )

    message = agent._format_stage_message(gate=gate, constraints=[], immovables=[])

    assert "<details>" not in message
    assert "</details>" not in message
    assert "<summary>" not in message
    assert "</summary>" not in message
    assert "Show all constraints" in message
    assert message.index("### Greetings") < message.index("### What I need from you")
