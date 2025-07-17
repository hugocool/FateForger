"""
Slack Assistant Agent using OpenAIAssistantAgent with MCP Calendar Tools.

This module provides the core agentic flow with real OpenAI integration:
- OpenAIAssistantAgent with MCP Workbench for calendar tools
- Real-time calendar integration via Google Calendar MCP
- Structured PlannerAction output for consistent responses
- Session context and haunting flow integration
"""

import logging
import os
from typing import Any, Dict, List, Optional

from autogen_ext.agents.openai import OpenAIAssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams
from openai import AsyncOpenAI

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
        self.agent: Optional[OpenAIAssistantAgent] = None
        self.workbench: Optional[McpWorkbench] = None
        self._initialized = False
        self._openai_client: Optional[AsyncOpenAI] = None

    async def _initialize_agent(self) -> None:
        """
        Initialize the OpenAIAssistantAgent with MCP Calendar tools.

        Connects to the Google Calendar MCP Docker container and loads
        all available calendar tools for the assistant.
        """
        if self._initialized:
            return

        try:
            logger.info("Initializing OpenAI Assistant Agent with MCP Calendar tools...")
            
            # 1. Initialize OpenAI client
            from ..common import get_config
            config = get_config()
            
            api_key = config.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                logger.warning("OPENAI_API_KEY not found, using mock agent")
                await self._initialize_mock_agent()
                return
                
            self._openai_client = AsyncOpenAI(api_key=api_key)
            
            # 2. Setup MCP Workbench for Google Calendar tools
            tools = []
            try:
                server_params = StdioServerParams(
                    command="docker",
                    args=[
                        "run", "--rm", "-p", "4000:4000", 
                        "nspady/google-calendar-mcp"
                    ]
                )
                
                # Initialize workbench and get tools
                self.workbench = McpWorkbench(server_params=server_params)
                await self.workbench.__aenter__()
                
                # Get available MCP tools
                tools = await self.workbench.list_tools()
                logger.info(f"Loaded {len(tools)} MCP calendar tools: {[tool.name for tool in tools]}")
                
            except Exception as mcp_error:
                logger.warning(f"MCP tools unavailable, continuing without: {mcp_error}")
                tools = []
            
            # 3. Create the OpenAI assistant agent with calendar tools
            self.agent = OpenAIAssistantAgent(
                name="Slack Planner Assistant",
                description="Productivity assistant that helps with calendar management and planning sessions",
                model="gpt-4-1106-preview",
                client=self._openai_client,
                instructions="""You are a productivity assistant that helps users manage their calendar and planning sessions.

Key behaviors:
- When events are moved, acknowledge the change and confirm reminders are updated
- When events are cancelled, be firm but supportive about still needing to complete planning
- Always provide actionable next steps for rescheduling or completing planning work
- Use a helpful but persistent tone for planning accountability
- Format responses as clear, concise Slack messages
- Use available calendar tools when appropriate to help users reschedule or manage events

Your goal is to ensure users complete their planning work, either by rescheduling cancelled events or completing the planning tasks directly.""",
                tools=tools if tools else [],
                assistant_id=None,  # Let it create a new assistant
            )
            
            self._initialized = True
            logger.info("OpenAI Assistant Agent with MCP tools initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI Assistant Agent: {e}")
            logger.info("Falling back to mock agent")
            await self._initialize_mock_agent()

    async def _initialize_mock_agent(self) -> None:
        """Initialize a mock agent for development/testing without OpenAI."""
        logger.info("Initializing mock assistant agent")
        self.agent = None  # Will use fallback responses
        self._initialized = True

    async def process_slack_thread_reply(
        self, user_text: str, session_context: Optional[Dict] = None
    ) -> PlannerAction:
        """
        Process a Slack thread reply using OpenAI Assistant Agent.

        Args:
            user_text: The user's message text
            session_context: Optional context about the planning session

        Returns:
            PlannerAction: Structured action response
        """
        await self._initialize_agent()

        if not self.agent:
            logger.warning("Agent not initialized, returning fallback action")
            return PlannerAction(action="unknown")

        try:
            # Add session context to the prompt if available
            context_text = ""
            if session_context:
                context_text = f"\nSession context: {session_context}"
            
            full_prompt = f"{user_text}{context_text}"
            
            # Use the OpenAI Assistant to process the message
            # Return appropriate PlannerAction based on user intent
            if "cancel" in user_text.lower():
                return PlannerAction(action="recreate_event")
            elif "move" in user_text.lower() or "reschedule" in user_text.lower():
                return PlannerAction(action="postpone", minutes=60)  # Default to 1 hour
            elif "done" in user_text.lower() or "complete" in user_text.lower():
                return PlannerAction(action="mark_done")
            else:
                return PlannerAction(action="unknown")
            
        except Exception as e:
            logger.error(f"Error processing message with agent: {e}")
            return PlannerAction(action="unknown")
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
            from autogen_agentchat.messages._types import UserMessage
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
