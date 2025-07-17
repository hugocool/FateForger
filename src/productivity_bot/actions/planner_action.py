"""
Planner Action data model and system prompt.

This module defines the PlannerAction Pydantic model and its associated
system prompt template for structured LLM output.
"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    """Valid planner action types."""

    POSTPONE = "postpone"
    MARK_DONE = "mark_done"
    RECREATE_EVENT = "recreate_event"
    UNKNOWN = "unknown"


class PlannerAction(BaseModel):
    """
    Structured action for planner bot responses.

    This model enforces strict schema validation for LLM outputs,
    ensuring all responses are valid and parseable without fallback logic.
    """

    action: Literal["postpone", "mark_done", "recreate_event", "unknown"] = Field(
        description="The action type to perform"
    )
    minutes: Optional[int] = Field(
        default=None,
        description="Minutes for postpone action (null for other actions)",
        ge=1,  # Must be positive if provided
        le=1440,  # Max 24 hours
    )

    @property
    def is_postpone(self) -> bool:
        """Check if this is a postpone action."""
        return self.action == "postpone"

    @property
    def is_mark_done(self) -> bool:
        """Check if this is a mark done action."""
        return self.action == "mark_done"

    @property
    def is_recreate_event(self) -> bool:
        """Check if this is a recreate event action."""
        return self.action == "recreate_event"

    @property
    def is_unknown(self) -> bool:
        """Check if this is an unknown action."""
        return self.action == "unknown"

    def get_postpone_minutes(self) -> Optional[int]:
        """Get postpone minutes, defaulting to 15 if postpone action without minutes."""
        if self.is_postpone:
            return self.minutes or 15
        return None

    def __str__(self) -> str:
        """String representation for logging."""
        if self.is_postpone:
            return f"postpone({self.get_postpone_minutes()}min)"
        return self.action


# Jinja2 template for the system prompt
PLANNER_SYSTEM_MESSAGE_TEMPLATE = """
<role>
Parse Slack thread replies in planning sessions into valid JSON objects matching the PlannerAction schema. Only return JSON, never explanations or extra text.
</role>

<schema>
{{ schema | tojson(indent=2) }}
</schema>

<actions>
<action name="postpone">
    <desc>Postpone planning session; extract minutes (int), default to 15 if not specified.</desc>
    <examples>
    postpone 15, delay 10 minutes, gimme 5, snooze for 20, revisit in an hour, 5 mins, 1 hour, later please, wait, pick it up in 5, come back in 10, not now, maybe later
    </examples>
</action>
<action name="mark_done">
    <desc>Mark session as complete.</desc>
    <examples>
    done, finished, complete, all set, yes, good, wrap up, all good, end, finalized, ready, ok, perfect
    </examples>
</action>
<action name="recreate_event">
    <desc>Recreate or remake event in calendar.</desc>
    <examples>
    recreate event, reschedule, new event, add to calendar, redo, restart, create calendar entry, remake, schedule again
    </examples>
</action>
<action name="unknown">
    <desc>Use when user intent is unclear or doesn't match other actions.</desc>
    <examples>
    unclear messages, random text, questions, unrelated content
    </examples>
</action>
</actions>

<rules>
<rule>Use ONLY valid JSON matching the PlannerAction schema above.</rule>
<rule>Return one action per message. No markdown, explanations, or extra text.</rule>
<rule>When action="postpone" and minutes is missing from user input, use 15.</rule>
<rule>If user intent is unclear, use {"action": "unknown", "minutes": null}.</rule>
<rule>Always ensure output validates against the schema.</rule>
<rule>Parse colloquial expressions like "gimme 5", "pick it up in 10", "not now" appropriately.</rule>
</rules>
""".strip()


# Rendered system message with schema
def get_planner_system_message() -> str:
    """Get the rendered system message with PlannerAction schema injected."""
    from .prompt_utils import prompt_renderer

    return prompt_renderer.render_with_schema(
        PLANNER_SYSTEM_MESSAGE_TEMPLATE, PlannerAction
    )


# Pre-rendered for convenience (call get_planner_system_message() for latest)
PLANNER_SYSTEM_MESSAGE = get_planner_system_message()
