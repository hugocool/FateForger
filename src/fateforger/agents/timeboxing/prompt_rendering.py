"""Prompt rendering helpers for timeboxing stage agents."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Template

from fateforger.agents.timeboxing.contracts import SkeletonContext
from fateforger.llm.toon import toon_encode

from fateforger.agents.timeboxing.toon_views import (
    constraints_rows,
    immovables_rows,
    tasks_rows,
 )


@lru_cache(maxsize=4)
def _load_template(path: str) -> Template:
    """Load and compile a Jinja template from the timeboxing package directory."""
    template_path = Path(__file__).with_name(path)
    raw = template_path.read_text(encoding="utf-8")
    return Template(raw)


def render_skeleton_draft_system_prompt(*, context: SkeletonContext) -> str:
    """Render the skeleton draft system prompt for the given context."""
    tpl = _load_template("skeleton_draft_system_prompt.j2")
    frame_toon = toon_encode(
        name="frame",
        rows=[
            {
                "date": context.date.isoformat(),
                "timezone": context.timezone,
                "work_start": (context.work_window.start if context.work_window else ""),
                "work_end": (context.work_window.end if context.work_window else ""),
                "sleep_start": (context.sleep_target.start if context.sleep_target else ""),
                "sleep_end": (context.sleep_target.end if context.sleep_target else ""),
                "sleep_hours": (context.sleep_target.hours if context.sleep_target else ""),
            }
        ],
        fields=[
            "date",
            "timezone",
            "work_start",
            "work_end",
            "sleep_start",
            "sleep_end",
            "sleep_hours",
        ],
    )
    block_plan_toon = toon_encode(
        name="block_plan",
        rows=[
            {
                "deep_blocks": (context.block_plan.deep_blocks if context.block_plan else ""),
                "shallow_blocks": (
                    context.block_plan.shallow_blocks if context.block_plan else ""
                ),
                "block_minutes": (
                    context.block_plan.block_minutes if context.block_plan else ""
                ),
                "focus_theme": (context.block_plan.focus_theme if context.block_plan else ""),
            }
        ]
        if context.block_plan
        else [],
        fields=["deep_blocks", "shallow_blocks", "block_minutes", "focus_theme"],
    )
    daily_one_thing_toon = toon_encode(
        name="daily_one_thing",
        rows=[
            {
                "title": context.daily_one_thing.title,
                "block_count": context.daily_one_thing.block_count or "",
                "duration_min": context.daily_one_thing.duration_min or "",
            }
        ]
        if context.daily_one_thing
        else [],
        fields=["title", "block_count", "duration_min"],
    )
    tasks_toon = toon_encode(
        name="tasks",
        rows=tasks_rows(context.tasks or []),
        fields=["title", "block_count", "duration_min", "due", "importance"],
    )
    immovables_toon = toon_encode(
        name="immovables",
        rows=immovables_rows(context.immovables or []),
        fields=["title", "start", "end"],
    )
    constraints_toon = toon_encode(
        name="constraints",
        rows=constraints_rows(context.constraints_snapshot or []),
        fields=["name", "necessity", "scope", "status", "source", "description"],
    )
    return (
        tpl.render(
            frame_toon=frame_toon,
            block_plan_toon=block_plan_toon,
            daily_one_thing_toon=daily_one_thing_toon,
            tasks_toon=tasks_toon,
            immovables_toon=immovables_toon,
            constraints_toon=constraints_toon,
        ).strip()
    )


__all__ = ["render_skeleton_draft_system_prompt"]
