"""
Commitment Action schema and prompt for planning commitment sessions.

This module defines the action schema and system prompt for parsing
user responses when they've committed to a planning time.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class HaunterActionBase(BaseModel):
    """Base class for all haunter action schemas."""

    action: str = Field(description="The action type to perform")


class CommitmentAction(HaunterActionBase):
    """
    Action schema for commitment planning sessions.

    Commitment sessions remind users about planning times they've already committed to.
    Users can mark the planning as done or postpone it.
    """

    action: Literal["mark_done", "postpone", "unknown"] = Field(
        description="Action to take for commitment session"
    )
    minutes: Optional[int] = Field(
        default=None,
        description="Minutes to postpone (for postpone action)",
        ge=1,
        le=240,  # Max 4 hours
    )

    @property
    def is_mark_done(self) -> bool:
        """Check if this is a mark done action."""
        return self.action == "mark_done"

    @property
    def is_postpone(self) -> bool:
        """Check if this is a postpone action."""
        return self.action == "postpone"

    @property
    def is_unknown(self) -> bool:
        """Check if this is an unknown action."""
        return self.action == "unknown"

    def get_postpone_minutes(self) -> Optional[int]:
        """Get postpone minutes, defaulting to 15 if postpone action without minutes."""
        if self.is_postpone:
            return self.minutes or 15
        return None


# System prompt for commitment intent parsing
COMMITMENT_PROMPT = """
<role>
You are parsing user responses in commitment planning sessions. The user has already committed to a specific planning time and this is a reminder.

Parse the user's reply into a valid JSON object matching the CommitmentAction schema. Only return JSON, never explanations.
</role>

<schema>
{
  "type": "object",
  "properties": {
    "action": {
      "type": "string",
      "enum": ["mark_done", "postpone", "unknown"],
      "description": "Action to take for commitment session"
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
<action name="mark_done">
    <desc>User has completed their planning or indicates they're done.</desc>
    <examples>
    done, finished, complete, all set, yes, good, wrapped up, ready, finished planning, completed, all good, perfect, yes done
    </examples>
</action>
<action name="postpone">
    <desc>User wants to delay the planning session - extract delay in minutes, default to 15.</desc>
    <examples>
    postpone 15, delay 30 minutes, not now, later, in 20 minutes, give me an hour, postpone for 45, maybe in 2 hours, ask again in 30
    </examples>
</action>
<action name="unknown">
    <desc>User response is unclear or doesn't match planning context.</desc>
    <examples>
    what?, I don't understand, unclear messages, random text, questions about other topics, help
    </examples>
</action>
</actions>

<rules>
<rule>Use ONLY valid JSON matching the CommitmentAction schema above.</rule>
<rule>When action="postpone" and no specific time given, use 15 minutes default.</rule>
<rule>When user indicates completion or satisfaction, use "mark_done".</rule>
<rule>If user intent is unclear or off-topic, use {"action": "unknown"}.</rule>
<rule>Always ensure output validates against the schema.</rule>
<rule>Parse colloquial time expressions like "gimme 30", "not now" appropriately.</rule>
</rules>
""".strip()
