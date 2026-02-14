"""Regression tests for Stage 3 markdown block rendering."""

from __future__ import annotations

from datetime import date, time

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.tb_models import AfterPrev, ET, FixedWindow, TBEvent, TBPlan


def test_render_markdown_summary_blocks_uses_markdown_block_type() -> None:
    """Stage 3 overview should be emitted as a Slack markdown block."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    blocks = TimeboxingFlowAgent._render_markdown_summary_blocks(
        agent,
        text="## Day Overview\n- Focus block",
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "markdown"
    assert blocks[0]["text"] == "## Day Overview\n- Focus block"


def test_tb_plan_overview_markdown_prefers_coarse_duration_for_flexible_blocks() -> None:
    """Stage 3 fallback markdown should show anchored times + coarse flexible durations."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    plan = TBPlan(
        date=date(2026, 2, 14),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Anchor Meeting",
                t=ET.M,
                p=FixedWindow(st=time(9, 0), et=time(10, 0)),
            ),
            TBEvent(
                n="Deep Work",
                t=ET.DW,
                p=AfterPrev(dur="PT90M"),
            ),
        ],
    )

    text = TimeboxingFlowAgent._tb_plan_overview_markdown(agent, plan)

    assert "09:00-10:00 **Anchor Meeting**" in text
    assert "**Deep Work** — ~1h30m" in text
    assert "10:00-11:30 **Deep Work**" not in text
