from __future__ import annotations

import logging
from typing import Awaitable, Callable

from autogen_core import MessageContext

from .messages import UserFacingMessage


logger = logging.getLogger(__name__)

DeliverySink = Callable[[UserFacingMessage, MessageContext], Awaitable[None]]

_delivery_sink: DeliverySink | None = None


def set_delivery_sink(sink: DeliverySink | None) -> None:
    """Set the global delivery sink for user-facing messages."""

    global _delivery_sink
    _delivery_sink = sink


async def deliver_user_facing(message: UserFacingMessage, ctx: MessageContext) -> None:
    """Deliver a user-facing message via the configured sink (or log fallback)."""

    if _delivery_sink:
        await _delivery_sink(message, ctx)
        return
    logger.info("UserFacingMessage (no delivery sink): %s", message.content)


__all__ = ["DeliverySink", "deliver_user_facing", "set_delivery_sink"]

