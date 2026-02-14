"""Unit tests for notebook-oriented timeboxing entrypoints."""

from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.notebook_entrypoints import (
    create_session,
    stage3_framework_report,
    stage3_method_locations,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


def test_create_session_defaults_to_stage3() -> None:
    """Notebook session helper should default to Skeleton stage."""
    session = create_session()
    assert session.stage == TimeboxingStage.SKELETON
    assert session.planned_date is not None
    assert session.session_key


def test_stage3_method_locations_include_expected_entrypoints() -> None:
    """Method location map should include core Stage 3 call-path entrypoints."""
    locations = stage3_method_locations()
    required = {
        "agent._run_skeleton_draft",
        "agent._run_skeleton_overview_markdown",
        "agent._build_skeleton_seed_plan",
        "agent._consume_pre_generated_skeleton",
        "node.StageSkeletonNode.on_messages",
    }
    assert required.issubset(set(locations.keys()))
    for key in required:
        assert locations[key].line > 0
        assert locations[key].file_path


def test_stage3_framework_report_signals_framework_usage() -> None:
    """Framework report should flag expected AutoGen and patch-loop wiring."""
    report = stage3_framework_report()
    assert report["uses_autogen_assistant_for_markdown"] is True
    assert report["uses_patcher_for_plan_draft"] is True
    assert report["stage3_presentation_first_node"] is True
    assert report["stage3_slack_markdown_block_path"] is True
    assert report["stage3_has_no_direct_timebox_validator_in_draft_call"] is True

