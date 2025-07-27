"""Planner intent agent using structured output."""

import logging
import os
from typing import Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

from ..actions.planner_action import (
    PlannerAction,
    get_planner_system_message,
)
from ..common import get_logger

logger = get_logger("planner_agent")

_planner_agent: Optional[AssistantAgent] = None


def build_planner_agent(api_key: str) -> AssistantAgent:
    client = OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        api_key=api_key,
        response_format=PlannerAction,
    )
    return AssistantAgent(
        name="planner_agent",
        model_client=client,
        system_message=get_planner_system_message(),
        output_content_type=PlannerAction,
    )


def _get_agent() -> AssistantAgent:
    global _planner_agent
    if _planner_agent is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        _planner_agent = build_planner_agent(api_key)
    return _planner_agent


async def send_to_planner_intent(user_text: str) -> PlannerAction:
    """Parse user text into a PlannerAction using the planner agent."""
    agent = _get_agent()
    logger.info(f"Processing user input: '{user_text}'")
    try:
        result = await agent.run(task=user_text)
        return result.chat_message.content
    except Exception as e:
        logger.error(f"Planner agent failed: {e}")
        return PlannerAction(action="unknown")
