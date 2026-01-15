"""Agent-as-tool wrapper for calendar submission."""

from __future__ import annotations

import logging
from typing import Any, Dict

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

from fateforger.core.config import settings

logger = logging.getLogger(__name__)


def build_calendar_submitter() -> AssistantAgent:
    """Return an assistant that calls the calendar MCP tools to materialize a plan."""

    system_prompt = (
        "You are CalendarSubmitter. Given a validated timebox JSON, call the available"
        " calendar MCP tools to create or update events. Respond with a JSON object"
        " {\"ok\": true, \"events\": [...]} on success, or {\"ok\": false, \"error\": str}"
        " if scheduling fails."
    )

    # Placeholder: in production, attach actual MCP tools.
    # For now, the assistant simply echos success to enable wiring tests.
    return AssistantAgent(
        name="CalendarSubmitter",
        system_message=system_prompt,
        model_client=OpenAIChatCompletionClient(
            model="gpt-4o-mini", api_key=settings.openai_api_key
        ),
        reflect_on_tool_use=False,
        max_tool_iterations=1,
    )


async def submit_plan(submitter: AssistantAgent, plan_json: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the submitter agent using the current plan JSON.

    NOTE: This is currently a stub that echoes success so the surrounding workflow can
    execute. Replace with actual MCP tool calls when wiring the calendar backend.
    """

    logger.info("Submitting plan with %d blocks", len(plan_json.get("blocks", [])))
    return {"ok": True, "events": plan_json.get("blocks", [])}


__all__ = ["build_calendar_submitter", "submit_plan"]
