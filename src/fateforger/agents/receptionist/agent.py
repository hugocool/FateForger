"""Receptionist agent that triages user intent via AutoGen handoffs."""

from __future__ import annotations

import logging
from typing import List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Handoff as HandoffBase
from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_core import DefaultTopicId, MessageContext, RoutedAgent, message_handler
from autogen_ext.models.openai import OpenAIChatCompletionClient

from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.haunt.mixins import HauntAwareAgentMixin
from fateforger.haunt.models import FollowUpPlan, HauntTone
from fateforger.haunt.orchestrator import HauntOrchestrator, HauntTicket


logger = logging.getLogger(__name__)


RECEPTIONIST_PROMPT = """
You are the FateForger Receptionist. Your job is to greet the user, understand
their request, and hand them off to the right specialist by using the
handoff tools that are available to you. Keep your replies short and
action-oriented. When you determine that a specialist should take over,
use the appropriate handoff tool. If you do not have enough information to
select a specialist, ask a brief clarification question.

Routing guidelines:
- If the user wants a concrete schedule for a day (timeboxing, time blocks, plan tomorrow/today with specific blocks), hand off to `timeboxing_agent`.
- If the user wants to inspect or edit calendar events (what's on my calendar, create/move/delete an event, find a slot), hand off to `planner_agent`.
""".strip()


class ReceptionistAgent(HauntAwareAgentMixin, RoutedAgent):
    """RoutedAgent wrapper around an AssistantAgent with handoff tools."""

    def __init__(
        self,
        name: str,
        *,
        allowed_handoffs: List[HandoffBase],
        haunt: HauntOrchestrator,
    ) -> None:
        RoutedAgent.__init__(self, name)
        HauntAwareAgentMixin.__init__(
            self,
            haunt_orchestrator=haunt,
            haunt_agent_id=f"receptionist::{name}",
            default_channel="reception",
        )
        if not allowed_handoffs:
            raise ValueError("ReceptionistAgent requires at least one handoff target")

        self._assistant = AssistantAgent(
            name=f"{name}_assistant",
            system_message=RECEPTIONIST_PROMPT,
            model_client=OpenAIChatCompletionClient(
                model="gpt-4o-mini", api_key=settings.openai_api_key
            ),
            handoffs=allowed_handoffs,
            reflect_on_tool_use=False,
            max_tool_iterations=3,
        )

    @message_handler
    async def handle_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage | HandoffMessage:
        logger.debug(
            "Receptionist received message from %s: %s", message.source, message.content
        )

        session_id = self._session_id(ctx)
        await self._log_inbound(
            session_id=session_id,
            content=message.content,
            core_intent=self._summarize(message.content),
        )

        response = await with_timeout(
            "receptionist:on_messages",
            self._assistant.on_messages([message], ctx.cancellation_token),
            timeout_s=20,
        )

        chat_message = response.chat_message
        if not isinstance(chat_message, (TextMessage, HandoffMessage)):
            logger.warning(
                "Receptionist assistant returned unsupported message type %s; falling back to text",
                type(chat_message),
            )
            return TextMessage(
                content="I'm having trouble forwarding your request right now. Could you rephrase it?"
            )
        if isinstance(chat_message, TextMessage):
            await self._log_outbound(
                session_id=session_id,
                content=chat_message.content,
                core_intent=self._summarize(chat_message.content),
                follow_up=self._follow_up_plan(chat_message),
                tone=HauntTone.ENCOURAGING,
            )
        else:
            await self._log_outbound(
                session_id=session_id,
                content=f"handoff:{chat_message.target.name}",
                core_intent=f"Hand off to {chat_message.target.name}",
                follow_up=FollowUpPlan(required=False),
                tone=HauntTone.NEUTRAL,
            )

        return chat_message

    @staticmethod
    def _session_id(ctx: MessageContext) -> str:
        topic = getattr(ctx, "topic_id", None)
        if topic:
            return str(topic)
        convo = getattr(ctx, "conversation_id", None)
        if convo:
            return str(convo)
        return "reception"

    @staticmethod
    def _summarize(text: str) -> str:
        clean = " ".join(text.split())
        return clean[:160]

    @staticmethod
    def _follow_up_plan(message: TextMessage) -> FollowUpPlan:
        if "?" in message.content:
            return FollowUpPlan(required=True, delay_minutes=5)
        return FollowUpPlan(required=True, delay_minutes=15)

    async def on_haunt_follow_up(self, ticket: HauntTicket) -> None:
        content = f"👻 Just checking in: {ticket.payload.core_intent}"
        await self.publish_message(
            TextMessage(content=content, source=self._haunt_agent_id),
            DefaultTopicId(),
        )


__all__ = ["ReceptionistAgent", "HandoffBase"]
