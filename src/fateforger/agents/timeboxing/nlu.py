"""Structured multilingual interpretation helpers for timeboxing.

This module replaces deterministic, English-only parsing for:
- planned date interpretation (e.g. "tomorrow", "next Monday", other languages)
- constraint intent + scope inference (session/profile/datespan) from natural language

All interpretation uses structured LLM outputs (Pydantic) and avoids keyword/regex intent logic.
"""

from __future__ import annotations

from typing import Literal, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from pydantic import BaseModel, Field

from fateforger.agents.timeboxing.preferences import ConstraintBase


class PlannedDateResult(BaseModel):
    """Structured result for interpreting a user's intended planning date."""

    planned_date: Optional[str] = Field(
        default=None, description="ISO date (YYYY-MM-DD) if confidently inferred"
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Confidence in planned_date"
    )
    timezone: Optional[str] = Field(
        default=None, description="IANA timezone if the user explicitly referenced one"
    )
    language: Optional[str] = Field(
        default=None, description="Optional BCP-47 language tag for telemetry"
    )
    explanation: Optional[str] = Field(
        default=None, description="Short debug explanation; not shown to the user"
    )


ConstraintScopeLiteral = Literal["session", "profile", "datespan"]


class ConstraintInterpretation(BaseModel):
    """Interpretation result for constraint extraction + scope inference."""

    should_extract: bool = Field(
        description="True only if the user explicitly stated scheduling constraints/preferences"
    )
    scope: ConstraintScopeLiteral = Field(
        description="session (this thread), profile (durable), or datespan (bounded period)"
    )
    start_date: Optional[str] = Field(
        default=None, description="ISO date (YYYY-MM-DD) when scope=datespan"
    )
    end_date: Optional[str] = Field(
        default=None, description="ISO date (YYYY-MM-DD) when scope=datespan"
    )
    constraints: list[ConstraintBase] = Field(default_factory=list)
    language: Optional[str] = Field(default=None)
    explanation: Optional[str] = Field(default=None)


PLANNED_DATE_INTERPRETER_PROMPT = """
You are Schedular, interpreting which DATE the user wants to plan.

Task
- Interpret the user message in ANY language.
- Output STRICT JSON matching PlannedDateResult.

Rules
- Only set planned_date when the user explicitly indicates a date (relative or absolute).
- If uncertain, set planned_date=null and confidence<=0.4.
- Use ISO date format YYYY-MM-DD.
- Use the provided timezone for resolving relative dates unless the user explicitly mentions a different timezone.
- Do not invent dates.
""".strip()


CONSTRAINT_INTERPRETER_PROMPT = """
You are Schedular, interpreting whether a message contains explicit scheduling constraints/preferences and what scope the user intended.

Task
- Interpret the user message in ANY language.
- Output STRICT JSON matching ConstraintInterpretation.

Definitions
- should_extract: true only if the user explicitly states a scheduling constraint or preference as THEIR own.
- scope:
  - session: applies only to this timeboxing session / thread
  - profile: durable preference ("in general", "always", "usually", "from now on") in any language
  - datespan: applies to a bounded period the user indicates ("this week", "next 2 weeks", date range) in any language

Rules
- Never extract from generic "start timeboxing" messages, greetings, or meta-chat about the bot.
- Extract only what the user stated; do not infer missing times or add new rules.
- Return constraints=[] if should_extract=false.
- If scope=datespan, include start_date/end_date as ISO dates if the user provided enough info; otherwise keep them null.
""".strip()


def build_planned_date_interpreter(
    *, model_client: OpenAIChatCompletionClient
) -> AssistantAgent:
    """Build the planned-date interpreter agent."""
    return AssistantAgent(
        name="PlanningDateInterpreter",
        model_client=model_client,
        output_content_type=PlannedDateResult,
        system_message=PLANNED_DATE_INTERPRETER_PROMPT,
        reflect_on_tool_use=False,
        max_tool_iterations=1,
    )


def build_constraint_interpreter(
    *, model_client: OpenAIChatCompletionClient
) -> AssistantAgent:
    """Build the constraint interpreter agent."""
    return AssistantAgent(
        name="ConstraintInterpreter",
        model_client=model_client,
        output_content_type=ConstraintInterpretation,
        system_message=CONSTRAINT_INTERPRETER_PROMPT,
        reflect_on_tool_use=False,
        max_tool_iterations=1,
    )


__all__ = [
    "ConstraintInterpretation",
    "ConstraintScopeLiteral",
    "PlannedDateResult",
    "build_constraint_interpreter",
    "build_planned_date_interpreter",
]

