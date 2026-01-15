from __future__ import annotations

from typing import Any

from autogen_core import AgentId, DefaultInterventionHandler, MessageContext

from .messages import UserFacingMessage
from .service import HauntingService


class HauntingInterventionHandler(DefaultInterventionHandler):
    """Interception handler that schedules and cancels follow-ups."""

    def __init__(
        self,
        service: HauntingService,
        *,
        user_channel_type: str = "user_channel",
        user_agent_type: str | None = None,
    ) -> None:
        self._service = service
        self._user_channel_type = user_channel_type
        self._user_agent_type = user_agent_type or user_channel_type

    async def on_send(
        self, message: Any, *, message_context: MessageContext, recipient: AgentId
    ) -> Any:
        sender = message_context.sender
        if sender and sender.type == self._user_agent_type:
            task_id = getattr(message, "task_id", None)
            await self._service.record_user_activity(
                topic_id=message_context.topic_id,
                task_id=task_id,
                user_id=getattr(message, "user_id", None),
            )

        if recipient.type == self._user_channel_type and isinstance(
            message, UserFacingMessage
        ):
            followup = message.followup
            if followup and followup.should_schedule:
                await self._service.schedule_followup(
                    message_id=message_context.message_id,
                    topic_id=message_context.topic_id,
                    task_id=message.task_id,
                    user_id=message.user_id,
                    channel_id=message.channel_id,
                    content=message.content,
                    spec=followup,
                )

        return message


__all__ = ["HauntingInterventionHandler"]
