"""
Planner Agent for processing user messages in planning threads.

This module provides structured LLM intent parsing using OpenAI's
Structured Outputs with Pydantic validation.
"""

import logging
import os
from typing import Optional

import openai

from ..common import get_logger
from ..models.planner_action import PlannerAction

logger = get_logger("planner_agent")

SYSTEM_MESSAGE = """You are the productivity planner assistant. 

Parse the user's reply into a structured PlannerAction. You can only respond with these exact actions:

1. postpone - When user wants to delay the planning session
   - Include "minutes" field with the number of minutes to postpone
   - Examples: "postpone 15", "delay for 30 minutes", "later" 

2. mark_done - When user indicates they're finished
   - Examples: "done", "finished", "complete", "I'm done"

3. recreate_event - When user wants to recreate the calendar event  
   - Examples: "recreate event", "create again", "reschedule"

Always respond with valid JSON that matches the PlannerAction schema. If the user's intent is unclear, choose the most likely action based on context.
"""


def get_openai_client():
    """Get OpenAI client configured for structured output."""
    return openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


async def send_to_planner_intent(user_text: str) -> PlannerAction:
    """
    Sends the raw user message to OpenAI using the PlannerAction model for structured output.

    This function uses OpenAI's Structured Outputs feature to ensure the response
    is always a valid PlannerAction instance, eliminating parsing errors.

    Args:
        user_text: The user's natural language input

    Returns:
        PlannerAction: Validated structured action

    Raises:
        Exception: If LLM call fails or response is invalid

    Example:
        >>> action = await send_to_planner_intent("postpone 15")
        >>> print(action.action, action.minutes)
        postpone 15
    """
    try:
        logger.info(f"Processing user input with structured output: '{user_text}'")

        client = get_openai_client()

        # Use OpenAI's structured output with response_format
        response = await client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": user_text},
            ],
            response_format=PlannerAction,  # This ensures structured output
        )

        # Parse the structured response
        action = response.choices[0].message.parsed

        if action is None:
            logger.warning("OpenAI returned None for parsed response, using default")
            return PlannerAction(action="mark_done", minutes=None)

        logger.info(
            f"Structured output result: {action.action} (minutes: {action.minutes})"
        )
        return action

    except Exception as e:
        logger.error(f"Structured LLM parsing failed for '{user_text}': {e}")
        # Return a safe default
        return PlannerAction(action="mark_done", minutes=None)


async def test_planner_agent() -> bool:
    """
    Test the planner agent with a sample message.

    Returns:
        True if test successful, False otherwise
    """
    try:
        action = await send_to_planner_intent("postpone 10")
        expected_action = "postpone"
        return action.action == expected_action and action.minutes == 10
    except Exception as e:
        logger.error(f"Planner agent test failed: {e}")
        return False
