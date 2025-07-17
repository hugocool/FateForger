"""
Bootstrap Action schema and prompt for planning bootstrap sessions.

This module defines the action schema and system prompt for parsing
user responses in bootstrap planning sessions.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class HaunterActionBase(BaseModel):
    """Base class for all haunter action schemas."""

    action: str = Field(description="The action type to perform")


class BootstrapAction(HaunterActionBase):
    """
    Action schema for bootstrap planning sessions.

    Bootstrap sessions help users create initial planning events when none exist.
    The user can either commit to a specific time or postpone the session.
    """

    action: Literal["create_event", "postpone", "unknown"] = Field(
        description="Action to take for bootstrap session"
    )
    commit_time_str: Optional[str] = Field(
        default=None,
        description="Free-form time commitment string (e.g., 'tomorrow 8pm', 'after lunch')",
    )
    minutes: Optional[int] = Field(
        default=None,
        description="Minutes to postpone (for postpone action)",
        ge=1,
        le=240,  # Max 4 hours
    )

    @property
    def is_create_event(self) -> bool:
        """Check if this is a create event action."""
        return self.action == "create_event"

    @property
    def is_postpone(self) -> bool:
        """Check if this is a postpone action."""
        return self.action == "postpone"

    @property
    def is_unknown(self) -> bool:
        """Check if this is an unknown action."""
        return self.action == "unknown"

    def get_postpone_minutes(self) -> Optional[int]:
        """Get postpone minutes, defaulting to 20 if postpone action without minutes."""
        if self.is_postpone:
            return self.minutes or 20
        return None


# System prompt for bootstrap intent parsing
BOOTSTRAP_PROMPT = """
<role>
You are parsing user responses in bootstrap planning sessions. Bootstrap sessions help users create their first planning event when they haven't planned anything yet.

Parse the user's reply into a valid JSON object matching the BootstrapAction schema. Only return JSON, never explanations.
</role>

<schema>
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": ["create_event", "postpone", "unknown"],
      "description": "Action to take for bootstrap session"
    },
    "commit_time_str": {
      "type": "string",
      "description": "Free-form time commitment string"
    },
    "minutes": {
      "type": "integer",
      "minimum": 1,
      "maximum": 240,
      "description": "Minutes to postpone"
    }
  },
  "required": ["action"]
}
</schema>

<actions>
<action name="create_event">
    <desc>User commits to a specific time for planning - extract the time commitment string.</desc>
    <examples>
    tomorrow 8pm, after lunch, in 2 hours, tonight at 9, tomorrow morning, next week, monday 10am, after dinner, 3pm today, this evening
    </examples>
</action>
<action name="postpone">
    <desc>User wants to delay the bootstrap session - extract delay in minutes, default to 20.</desc>
    <examples>
    postpone 30, delay for an hour, maybe later, not now, in 20 minutes, ask me in 45, try again in 2 hours, check back in 90 minutes
    </examples>
</action>
<action name="unknown">
    <desc>User response is unclear or doesn't match planning intent.</desc>
    <examples>
    what?, I don't understand, unclear messages, random text, questions about other topics
    </examples>
</action>
</actions>

<rules>
<rule>Use ONLY valid JSON matching the BootstrapAction schema above.</rule>
<rule>When action="create_event", extract the time commitment in commit_time_str.</rule>
<rule>When action="postpone" and no specific time given, use 20 minutes default.</rule>
<rule>If user intent is unclear or off-topic, use {"action": "unknown"}.</rule>
<rule>Always ensure output validates against the schema.</rule>
<rule>Parse natural language time expressions like "after dinner", "tomorrow 8pm" literally.</rule>
</rules>
""".strip()
