"""Admonisher agent for accountability nudges and follow-through."""

from __future__ import annotations

import logging

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Handoff as HandoffBase
from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler

from fateforger.llm import build_autogen_chat_client

from .prompts import ADMONISHER_PERSONA_PROMPT

logger = logging.getLogger(__name__)

ADMONISHER_PROMPT = f"""
{ADMONISHER_PERSONA_PROMPT}

Operating rules:
- Be concise, practical, and persistent.
- Do not accept vague deferrals. If the user says "no" or "later", ask for a concrete time.
- Prefer proposing 2–3 concrete options and asking the user to pick one.
- If the user asks for timeboxing or a concrete daily plan, hand off to `timeboxing_agent`.
- If the user asks to schedule or change calendar events, hand off to `planner_agent`.
""".strip()


class AdmonisherAgent(RoutedAgent):
    def __init__(self, name: str, *, allowed_handoffs: list[HandoffBase]) -> None:
        super().__init__(name)
        self._assistant = AssistantAgent(
            name=f"{name}_assistant",
            system_message=ADMONISHER_PROMPT,
            model_client=build_autogen_chat_client("admonisher_agent"),
            handoffs=allowed_handoffs,
            reflect_on_tool_use=False,
            max_tool_iterations=2,
        )

    @message_handler
    async def handle_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage | HandoffMessage:
        logger.debug("Admonisher received message: %s", message.content)
        response = await self._assistant.on_messages([message], ctx.cancellation_token)
        chat_message = response.chat_message
        if isinstance(chat_message, (TextMessage, HandoffMessage)):
            return chat_message
        return TextMessage(content=str(getattr(chat_message, "content", "")))
