"""Tests for timeboxing prompt rendering and isolation."""

from __future__ import annotations

from datetime import date

from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.contracts import Immovable, SkeletonContext
from fateforger.agents.timeboxing.prompt_rendering import render_skeleton_draft_system_prompt
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintSource,
    ConstraintStatus,
)


def test_skeleton_prompt_is_single_purpose() -> None:
    """Skeleton prompt should be short and not contain cross-stage instructions."""
    context = SkeletonContext(
        date=date(2026, 1, 21),
        timezone="Europe/Amsterdam",
        constraints_snapshot=[
            Constraint(
                name="No meetings before 10",
                description="User does not do meetings before 10:00.",
                necessity=ConstraintNecessity.MUST,
                user_id="U1",
                status=ConstraintStatus.LOCKED,
                source=ConstraintSource.USER,
            )
        ],
        immovables=[Immovable(title="Gym", start="18:00", end="19:30")],
    )
    prompt = render_skeleton_draft_system_prompt(context=context)

    assert "Timeboxing Skeleton Drafter" in prompt
    assert "Do not ask questions" in prompt
    assert "Data (TOON format):" in prompt
    assert "constraints[" in prompt
    assert "immovables[" in prompt
    assert "frame[1]{date,timezone" in prompt

    # Avoid leaking generic multi-stage instructions into the skeleton drafter.
    forbidden = [
        "Stage: CollectConstraints",
        "Stage: CaptureInputs",
        "tool",
        "ticktick",
        "notion",
    ]
    lowered = prompt.lower()
    for term in forbidden:
        assert term.lower() not in lowered
