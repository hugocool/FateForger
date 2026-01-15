from __future__ import annotations

import logging
from typing import Awaitable, Callable

from autogen_core import AgentId, MessageContext, RoutedAgent, message_handler

from .messages import FollowUpDue, UserFacingMessage
from .service import HauntingService, PendingFollowUp

logger = logging.getLogger(__name__)

DeliverySink = Callable[[UserFacingMessage, MessageContext], Awaitable[None]]


class UserChannelAgent(RoutedAgent):
    """Boundary agent for user-facing output."""

    def __init__(
        self,
        name: str = "user_channel",
        *,
        deliver: DeliverySink | None = None,
    ) -> None:
        super().__init__(description=name)
        self._deliver = deliver or self._log_delivery

    @message_handler
    async def handle_user_facing(
        self, message: UserFacingMessage, ctx: MessageContext
    ) -> None:
        await self._deliver(message, ctx)

    async def _log_delivery(self, message: UserFacingMessage, _: MessageContext) -> None:
        logger.info("UserChannelAgent delivery: %s", message.content)


class HauntingAgent(RoutedAgent):
    """Handles follow-up due notifications and decides on user outreach."""

    def __init__(
        self,
        name: str,
        *,
        service: HauntingService,
        user_channel_type: str = "user_channel",
        default_channel_key: str = "default",
    ) -> None:
        super().__init__(description=name)
        self._service = service
        self._user_channel_type = user_channel_type
        self._default_channel_key = default_channel_key

    @message_handler
    async def handle_followup_due(
        self, message: FollowUpDue, _: MessageContext
    ) -> None:
        record = await self._service.get_followup(message.message_id)
        if not record:
            return

        content = self._format_followup(record, message)
        recipient_key = message.topic_id or self._default_channel_key
        recipient = AgentId(self._user_channel_type, key=recipient_key)
        await self.send_message(
            UserFacingMessage(
                content=content,
                task_id=record.task_id,
                user_id=record.user_id,
                channel_id=record.channel_id,
            ),
            recipient=recipient,
        )

    @staticmethod
    def _format_followup(record: PendingFollowUp, due: FollowUpDue) -> str:
        prefixes = {
            "gentle": "Just checking in",
            "firm": "Reminder",
            "menacing": "Following up",
        }
        prefix = prefixes.get(due.escalation or "gentle", "Checking in")
        return f"{prefix}: {record.content}"


__all__ = ["HauntingAgent", "UserChannelAgent"]
