"""Serialize typed tool results at the interaction boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .tool_result_models import InteractionMode, MemoryToolResult


class InteractionContext(BaseModel):
    """Channel context used to serialize tool output for the current interaction."""

    mode: InteractionMode
    user_id: str
    thread_ts: str


class MemoryToolPresentation(BaseModel):
    """Presentation envelope built from a typed memory tool result."""

    payload: dict[str, Any]
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    text_update: str | None = None


def present_memory_tool_result(
    *,
    result: MemoryToolResult,
    context: InteractionContext,
) -> MemoryToolPresentation:
    """Serialize the typed result for the active channel without mutating session state."""
    payload = result.to_tool_payload()
    match context.mode:
        case InteractionMode.SLACK:
            from fateforger.slack_bot.constraint_review import (
                build_memory_tool_result_blocks,
            )

            blocks = build_memory_tool_result_blocks(
                result,
                thread_ts=context.thread_ts,
                user_id=context.user_id,
            )
            return MemoryToolPresentation(payload=payload, blocks=blocks)
        case _:
            return MemoryToolPresentation(payload=payload, text_update=result.message)


__all__ = [
    "InteractionContext",
    "MemoryToolPresentation",
    "present_memory_tool_result",
]
