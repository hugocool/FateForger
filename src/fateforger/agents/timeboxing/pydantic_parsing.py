"""Pydantic parsing helpers used by the timeboxing coordinator.

These helpers centralize tolerant parsing/normalization so orchestration code can stay readable:
- Avoid repeated `try/except ValidationError` blocks sprinkled throughout `agent.py`.
- Provide predictable behavior: invalid items are skipped rather than failing the whole stage.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import TypeAdapter, ValidationError

T = TypeVar("T")


# TODO: this should not be neccesary, we should leverage the agents message type to get this to work
def parse_chat_content(model: type[T], response: Any) -> T:
    """Parse `response.chat_message.content` into `model`.

    Args:
        model: Target Pydantic/SQLModel type.
        response: AutoGen response object expected to carry `chat_message.content`.

    Returns:
        Parsed instance of `model`.

    Raises:
        ValidationError: if content cannot be parsed as `model`.
    """
    content = getattr(getattr(response, "chat_message", None), "content", None)
    if isinstance(content, model):
        return content
    return TypeAdapter(model).validate_python(content)


# TODO: this should not be neccesary, we should leverage the agents message type to get this to work
def parse_model_optional(model: type[T], value: Any) -> T | None:
    """Parse a single object into `model`, returning None on invalid/empty input.

    This is intentionally tolerant: it returns None rather than raising on validation errors.
    """
    if value is None:
        return None
    if isinstance(value, model):
        return value
    try:
        return TypeAdapter(model).validate_python(value)
    except ValidationError:
        return None


# TODO: this should not be neccesary, we should leverage the agents message type to get this to work
def parse_model_list(model: type[T], value: Any) -> list[T]:
    """Parse a list of objects into a list of `model`, skipping invalid items."""
    if not isinstance(value, list):
        return []
    adapter = TypeAdapter(model)
    out: list[T] = []
    for item in value:
        if isinstance(item, model):
            out.append(item)
            continue
        try:
            out.append(adapter.validate_python(item))
        except ValidationError:
            continue
    return out
