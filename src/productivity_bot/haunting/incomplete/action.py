"""
Incomplete Action schema and prompt for incomplete planning sessions.

This module defines the action schema and system prompt for parsing
user responses when planning sessions remain incomplete.
"""

from typing import Literal, Optional

from pydantic import Field
from fateforger.actions.base import ActionBase


class IncompleteAction(ActionBase):
    """
    Action schema for incomplete planning sessions.

    Incomplete sessions follow up on planning that was started but not completed.
    Users can only postpone since the planning is not yet done.
    """

    action: Literal["postpone", "unknown"] = Field(
        description="Action to take for incomplete session"
    )
    minutes: Optional[int] = Field(
        default=None,
        description="Minutes to postpone (for postpone action)",
        ge=1,
        le=240,  # Max 4 hours
    )

    @property
    def is_postpone(self) -> bool:
        """Check if this is a postpone action."""
        return self.action == "postpone"

    @property
    def is_unknown(self) -> bool:
        """Check if this is an unknown action."""
        return self.action == "unknown"

    def get_postpone_minutes(self) -> Optional[int]:
        """Get postpone minutes, defaulting to 30 if postpone action without minutes."""
        if self.is_postpone:
            return self.minutes or 30
        return None


# System prompt for incomplete intent parsing
INCOMPLETE_PROMPT = """
<role>
You are parsing user responses in incomplete planning sessions. The user started planning but didn't finish, so this is a follow-up reminder.

Parse the user's reply into a valid JSON object matching the IncompleteAction schema. Only return JSON, never explanations.
</role>

<schema>
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": ["postpone", "unknown"],
      "description": "Action to take for incomplete session"
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
<action name="postpone">
    <desc>User wants to continue planning later - extract delay in minutes, default to 30.</desc>
    <examples>
    postpone 30, delay for an hour, later please, not now, in 45 minutes, give me time, postpone for 90, maybe in 2 hours, ask again later, need more time
    </examples>
</action>
<action name="unknown">
    <desc>User response is unclear or doesn't indicate when to follow up.</desc>
    <examples>
    what?, I don't understand, unclear messages, random text, questions about other topics, help, confused
    </examples>
</action>
</actions>

<rules>
<rule>Use ONLY valid JSON matching the IncompleteAction schema above.</rule>
<rule>When action="postpone" and no specific time given, use 30 minutes default.</rule>
<rule>Any response indicating delay or "later" should be "postpone".</rule>
<rule>If user intent is unclear or off-topic, use {"action": "unknown"}.</rule>
<rule>Always ensure output validates against the schema.</rule>
<rule>Parse time expressions like "give me an hour", "later" as postpone requests.</rule>
</rules>
""".strip()
