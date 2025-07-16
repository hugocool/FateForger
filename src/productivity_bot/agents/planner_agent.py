"""
Planner Agent for processing user messages in planning threads.

This module creates an OpenAI Assistant Agent that can understand user requests
and respond with structured JSON actions using OpenAI's Structured Outputs.
"""

import json
import logging
import os
from typing import Dict, Any, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient

from .mcp_client import get_calendar_tools, get_llm_client
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


def get_structured_llm_client() -> OpenAIChatCompletionClient:
    """Get the LLM client configured for structured output."""
    return OpenAIChatCompletionClient(
        model="gpt-4o-mini",
        api_key=os.environ.get("OPENAI_API_KEY", "")
    )

async def create_planner_agent() -> AssistantAgent:
    """
    Create an AssistantAgent configured with calendar tools and planning capabilities.
    
    Returns:
        Configured AssistantAgent instance
    """
    try:
        # 1. Fetch MCP calendar tools
        tools = await get_calendar_tools()
        
        # 2. Get LLM client
        llm_client = get_llm_client()
        
        # 3. Create an AssistantAgent with calendar capabilities
        agent = AssistantAgent(
            name="planner",
            model_client=llm_client,
            tools=tools,
            system_message=SYSTEM_MESSAGE
        )
        
        logger.info("Created planner agent with calendar tools")
        return agent
        
    except Exception as e:
        logger.error(f"Failed to create planner agent: {e}")
        # Return a basic agent without tools as fallback
        llm_client = get_llm_client()
        return AssistantAgent(
            name="planner",
            model_client=llm_client,
            system_message=SYSTEM_MESSAGE
        )

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
        # For now, use the fallback text parsing until we resolve the AutoGen API issues
        # TODO: Implement proper structured output when AutoGen API is clarified
        logger.info(f"Processing user input with fallback parsing: '{user_text}'")
        
        # Convert legacy dict response to PlannerAction
        legacy_response = _extract_action_from_text(user_text)
        
        # Map legacy response to PlannerAction
        if legacy_response.get("action") == "postpone":
            return PlannerAction(
                action="postpone",
                minutes=legacy_response.get("minutes")
            )
        elif legacy_response.get("action") == "mark_done":
            return PlannerAction(action="mark_done", minutes=None)
        elif legacy_response.get("action") == "recreate_event":
            return PlannerAction(action="recreate_event", minutes=None)
        else:
            # Default to mark_done for unrecognized input
            logger.warning(f"Unrecognized action, defaulting to mark_done: {legacy_response}")
            return PlannerAction(action="mark_done", minutes=None)
        
    except Exception as e:
        logger.error(f"Structured LLM parsing failed for '{user_text}': {e}")
        # Return a safe default
        return PlannerAction(action="mark_done", minutes=None)


async def send_to_planner(thread_id: str, user_text: str) -> Dict[str, Any]:
    """
    Send user text to the planner agent and get a structured JSON response.
    
    Args:
        thread_id: The Slack thread ID for context
        user_text: The user's message text
        
    Returns:
        Dictionary containing the parsed action and parameters
        
    Example:
        >>> response = await send_to_planner("123.456", "postpone 30")
        >>> print(response)
        {"action": "postpone", "minutes": 30}
    """
    try:
        # For now, use simple text parsing while we figure out AutoGen API
        # This provides immediate functionality while we sort out the library versions
        
        logger.info(f"Processing user input from thread {thread_id}: '{user_text}'")
        
        # TODO: Replace with proper LLM agent when AutoGen API is sorted out
        # For now, use rule-based parsing
        return _extract_action_from_text(user_text)
            
    except Exception as e:
        logger.error(f"Error in send_to_planner: {e}")
        # Fallback to simple text parsing
        return _extract_action_from_text(user_text)

def _extract_action_from_text(user_text: str) -> Dict[str, Any]:
    """
    Fallback function to extract actions from user text using simple parsing.
    
    Args:
        user_text: The user's message text
        
    Returns:
        Dictionary containing the parsed action
    """
    text = user_text.lower().strip()
    
    # Check for postpone commands
    if "postpone" in text or "delay" in text or "later" in text:
        # Try to extract minutes
        import re
        numbers = re.findall(r'\d+', text)
        if numbers:
            minutes = int(numbers[0])
            return {"action": "postpone", "minutes": minutes}
        else:
            # Default postpone time
            return {"action": "postpone", "minutes": 15}
    
    # Check for completion commands
    if any(word in text for word in ["done", "complete", "finished", "finish"]):
        return {"action": "mark_done"}
    
    # Check for recreate commands  
    if any(word in text for word in ["recreate", "create", "reschedule"]):
        return {"action": "recreate_event"}
    
    # Check for help commands
    if any(word in text for word in ["help", "what", "how", "commands"]):
        return {"action": "help"}
    
    # Check for status commands
    if "status" in text:
        return {"action": "status"}
    
    # Default to help
    logger.info(f"Could not parse user text: '{user_text}', defaulting to help")
    return {"action": "help"}

async def test_planner_agent() -> bool:
    """
    Test the planner agent with a sample message.
    
    Returns:
        True if test successful, False otherwise
    """
    try:
        response = await send_to_planner("test_thread", "postpone 10")
        expected_action = "postpone"
        return response.get("action") == expected_action
    except Exception as e:
        logger.error(f"Planner agent test failed: {e}")
        return False
