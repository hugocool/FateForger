"""
Slack Assistant Agent using AssistantAgent with structured output enforcement.

This module provides the core agentic flow with guaranteed schema compliance:
- AssistantAgent with output_content_type=PlannerAction for strict schema enforcement
- No parsing needed - LLM output is guaranteed to match PlannerAction schema
- MCP Workbench integration for calendar tools (optional)
- Shared session management with haunter bot
"""

import logging
import os
from typing import Any, Dict, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import McpWorkbench, SseServerParams

from ..actions.planner_action import PlannerAction, get_planner_system_message
from ..common import get_logger
from ..session_manager import get_session_registry

logger = get_logger("slack_assistant_agent")


class SlackAssistantAgent:
    """
    Slack assistant agent with guaranteed structured output.

    This agent uses AssistantAgent with output_content_type=PlannerAction to ensure
    all LLM responses strictly conform to the PlannerAction schema. No parsing
    or manual JSON extraction is needed.

    Key Features:
    - Structured output enforcement via output_content_type
    - Optional MCP Workbench integration for calendar tools
    - Guaranteed PlannerAction schema compliance
    - Session context support
    """

    def __init__(self):
        """Initialize the Slack assistant agent."""
        self.agent: Optional[AssistantAgent] = None
        self.workbench: Optional[McpWorkbench] = None
        self._initialized = False

    async def _initialize_agent(self) -> None:
        """
        Initialize the AssistantAgent with structured output enforcement.

        Uses output_content_type=PlannerAction to guarantee schema compliance.
        Optionally discovers MCP calendar tools if server is available.
        """
        if self._initialized:
            return

        try:
            logger.info(
                "Initializing AssistantAgent with structured output enforcement..."
            )

            # 1. Initialize LLM client
            from ..common import get_config

            config = get_config()

            model_client = OpenAIChatCompletionClient(
                model="gpt-4o-mini",
                api_key=config.openai_api_key or os.environ.get("OPENAI_API_KEY", ""),
            )

            # 2. Configure MCP Workbench for calendar tools discovery
            workbench = None
            tools = []
            try:
                MCP_URL = "http://calendar-mcp:3000/mcp"
                server_params = SseServerParams(
                    url=MCP_URL, timeout=30, sse_read_timeout=300
                )
                workbench = McpWorkbench(server_params=server_params)

                async with workbench:
                    tools = await workbench.list_tools()
                logger.info(f"Discovered {len(tools)} MCP calendar tools")

                # Log tool names for debugging
                tool_names = [
                    tool["name"] if isinstance(tool, dict) else str(tool)
                    for tool in tools
                ]
                logger.debug(f"Available MCP tools: {tool_names}")
            except Exception as mcp_error:
                logger.warning(
                    f"MCP tools unavailable, continuing without: {mcp_error}"
                )
                tools = []
                workbench = None

            # 3. Create AssistantAgent with MCP integration and structured output
            # Key changes: workbench=workbench, reflect_on_tool_use=True for proper MCP integration
            agent_params = {
                "name": "planner",
                "model_client": model_client,
                "system_message": get_planner_system_message(),
                "output_content_type": PlannerAction,  # STRICT SCHEMA ENFORCEMENT
            }

            # Add MCP integration if workbench is available
            if workbench:
                agent_params["workbench"] = workbench
                agent_params["reflect_on_tool_use"] = True
                logger.info("AssistantAgent configured with MCP workbench integration")

            self.agent = AssistantAgent(**agent_params)
            self.workbench = workbench  # Store for later use

            self._initialized = True
            logger.info(
                "Successfully initialized AssistantAgent with structured output"
            )

        except Exception as e:
            logger.error(f"Failed to initialize AssistantAgent: {e}")
            raise

    async def process_slack_thread_reply(
        self, user_text: str, session_context: Optional[Dict] = None
    ) -> PlannerAction:
        """
        Process a Slack thread reply with guaranteed structured output.

        Uses AssistantAgent with output_content_type=PlannerAction to ensure
        the response always conforms to the schema. No parsing or validation
        errors are possible - the LLM is forced to return valid PlannerAction.

        Args:
            user_text: The user's message text
            session_context: Optional context about the planning session

        Returns:
            PlannerAction: Guaranteed valid structured action
        """
        await self._initialize_agent()

        if not self.agent:
            logger.error("Agent not initialized, returning default action")
            return PlannerAction(action="unknown", minutes=None)

        try:
            logger.info(
                f"Processing thread reply with structured output: '{user_text}'"
            )

            # Get session registry for shared session management
            session_registry = get_session_registry()

            # Build message with optional session context
            message_content = user_text
            if session_context:
                import json

                context_str = f"Planning session info: {json.dumps(session_context, default=str)}\n\nUser message: {user_text}"
                message_content = context_str

            # Use AssistantAgent with structured output enforcement
            # The agent is configured with output_content_type=PlannerAction
            from autogen_agentchat.messages import UserMessage
            from autogen_core import CancellationToken

            user_message = UserMessage(content=message_content, source="user")
            cancellation_token = CancellationToken()

            # Run the agent - this returns structured output guaranteed to match PlannerAction
            response = await self.agent.on_messages([user_message], cancellation_token)

            # Extract the PlannerAction from the structured response
            # With output_content_type=PlannerAction, the response content is guaranteed to be valid
            if response and response.chat_message:
                # The content should be a PlannerAction due to output_content_type enforcement
                chat_message = response.chat_message
                if hasattr(chat_message, "content"):
                    content = chat_message.content
                elif hasattr(chat_message, "model_dump"):
                    # Try to extract from model dump if direct access fails
                    message_data = chat_message.model_dump()
                    content = message_data.get("content")
                else:
                    content = None

                if isinstance(content, PlannerAction):
                    logger.info(f"Agent returned structured action: {content}")

                    # Handle special actions with session management
                    if (
                        content.is_mark_done
                        and session_context
                        and session_context.get("session_id")
                    ):
                        # Mark session as done and add emoji
                        await session_registry.mark_session_done(
                            session_context["session_id"], add_emoji=True
                        )
                        logger.info(
                            f"Marked session {session_context['session_id']} as complete"
                        )

                    return content
                else:
                    # This should never happen with proper structured output enforcement
                    logger.warning(
                        f"Unexpected response content type: {type(content)} - structured output not working"
                    )
                    return PlannerAction(action="unknown", minutes=None)
            else:
                logger.warning("Empty response from agent")
                return PlannerAction(action="unknown", minutes=None)

        except Exception as e:
            logger.error(f"Agent processing failed for '{user_text}': {e}")
            # Return safe default - even errors are valid PlannerAction objects
            return PlannerAction(action="unknown", minutes=None)

    async def cleanup(self) -> None:
        """Clean up MCP workbench resources."""
        if self.workbench:
            try:
                await self.workbench.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error cleaning up MCP workbench: {e}")
        self._initialized = False


# Global instance for use by SlackEventRouter
_slack_assistant_agent: Optional[SlackAssistantAgent] = None


async def get_slack_assistant_agent() -> SlackAssistantAgent:
    """Get the global SlackAssistantAgent instance."""
    global _slack_assistant_agent
    if _slack_assistant_agent is None:
        _slack_assistant_agent = SlackAssistantAgent()
    return _slack_assistant_agent


async def process_slack_thread_reply(
    user_text: str, session_context: Optional[Dict] = None
) -> PlannerAction:
    """
    Process a Slack thread reply using AssistantAgent with strict schema enforcement.

    This is the main entry point that replaces send_to_planner_intent().
    It uses AssistantAgent with output_content_type=PlannerAction to guarantee
    that all responses conform to the PlannerAction schema.

    Args:
        user_text: User's message in the planning thread
        session_context: Optional planning session context

    Returns:
        PlannerAction: Guaranteed valid structured action for scheduler execution
    """
    agent = await get_slack_assistant_agent()
    return await agent.process_slack_thread_reply(user_text, session_context)
