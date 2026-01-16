"""Revisor agent for weekly reviews, long-term project management, and agent optimization."""

from __future__ import annotations

import logging
from typing import List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler

from fateforger.llm import build_autogen_chat_client

logger = logging.getLogger(__name__)

REVISOR_PROMPT = """
You are the FateForger Revisor. Your role is long-term optimization and review.

Core Responsibilities:
1. **Weekly Reviews**: Facilitate the transition from one week to the next. Ask:
   - What went well?
   - What didn't go as planned?
   - What concretely needs to change?
   - What are the risks for the coming week?
2. **Agent Optimization**: Analyze interactions with other agents (Receptionist, Timeboxer, Planner) and suggest or implement configuration changes to improve their performance.
3. **Long-term Management**: Connect with Notion Projects and manage the "Life Management" project. Look beyond daily timeboxing to monthly and quarterly goals.
4. **Strategic Alignment**: Ensure daily actions (Timeboxing) align with long-term projects and life goals.

Tone: Professional, analytical, but encouraging. You are the high-level strategist of the user's life.
""".strip()


class RevisorAgent(RoutedAgent):
    """Agent that handles long-term planning and system-wide reviews."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._assistant = AssistantAgent(
            name=f"{name}_assistant",
            system_message=REVISOR_PROMPT,
            model_client=build_autogen_chat_client("revisor_agent"),
        )

    @message_handler
    async def handle_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage:
        logger.debug("Revisor received message: %s", message.content)

        # In a full implementation, we would integrate with Notion here
        # and potentially query the history of other agents.

        response = await self._assistant.on_messages([message], ctx.cancellation_token)
        return response.chat_message
