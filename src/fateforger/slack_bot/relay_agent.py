"""Agent that relays topic outputs into the Slack bridge queues."""

from __future__ import annotations

import logging
from typing import Any

from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler

from .topics import TopicRegistry
from fateforger.agents.timeboxing.messages import TimeboxingFinalResult


logger = logging.getLogger(__name__)


class SlackRelayAgent(RoutedAgent):
    """Collects messages published to Slack thread topics and enqueues them for Bolt."""

    def __init__(self, name: str = "slack_bridge") -> None:
        super().__init__(description=name)

    def _enqueue(self, ctx: MessageContext, payload: dict[str, Any]) -> None:
        registry = TopicRegistry.get_global()
        if not registry:
            logger.debug("No TopicRegistry available; dropping payload")
            return
        if not ctx.topic_id:
            logger.debug("No topic id in message context; dropping payload")
            return
        binding = registry.get_by_topic(ctx.topic_id)
        if not binding:
            logger.debug("No binding for topic %s; payload ignored", ctx.topic_id)
            return
        binding.queue.put_nowait(payload)

    @message_handler
    async def handle_text(self, message: TextMessage, ctx: MessageContext) -> None:
        self._enqueue(
            ctx,
            {
                "type": "text",
                "content": message.content,
                "source": message.source,
            },
        )

    @message_handler
    async def handle_final(self, message: TimeboxingFinalResult, ctx: MessageContext) -> None:
        self._enqueue(
            ctx,
            {
                "type": "final",
                "status": message.status,
                "summary": message.summary,
                "payload": message.payload,
            },
        )


__all__ = ["SlackRelayAgent"]

