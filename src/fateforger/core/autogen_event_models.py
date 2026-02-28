"""Pydantic models for structured AutoGen event payloads.

AutoGen emits structured events to ``autogen_core.events`` via Python's logging
framework.  These models replace ad-hoc ``dict.get()`` chains with typed,
validated payloads, enabling clean isinstance-based dispatch in
``_AutogenEventsFilter`` and ``StructuredJsonFormatter``.

Usage::

    from fateforger.core.autogen_event_models import parse_autogen_event, LLMEventPayload

    payload = ...  # dict from LogRecord.msg or kwargs
    event = parse_autogen_event(payload)
    if isinstance(event, LLMEventPayload):
        ...  # access event.model, event.prompt_tokens, etc.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _AutogenEventBase(BaseModel):
    """Common fields shared by all AutoGen event payloads."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    agent_id: str | None = None
    stage: str | None = None
    call_label: str | None = None
    session_key: str | None = None
    thread_ts: str | None = None
    channel_id: str | None = None


# ---------------------------------------------------------------------------
# LLM events  (type = "LLMCall" | "LLMStreamEnd")
# ---------------------------------------------------------------------------


class LLMEventPayload(_AutogenEventBase):
    """Payload for LLMCall and LLMStreamEnd events."""

    type: Literal["LLMCall", "LLMStreamEnd"]
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    messages: Any = None
    response: dict[str, Any] | None = None

    @computed_field  # type: ignore[misc]
    @property
    def response_error(self) -> str | None:
        """Return the error string from the response, or None if not errored."""
        if isinstance(self.response, dict):
            err = self.response.get("error")
            if isinstance(err, str) and err:
                return err
        return None

    @computed_field  # type: ignore[misc]
    @property
    def response_status(self) -> Literal["ok", "error"]:
        """Coerce to a Prometheus-friendly status label."""
        return "error" if self.response_error else "ok"

    @computed_field  # type: ignore[misc]
    @property
    def response_model(self) -> str:
        """Resolve the actual model name from response first, then model field."""
        if isinstance(self.response, dict):
            m = self.response.get("model")
            if isinstance(m, str) and m:
                return m
        if isinstance(self.model, str) and self.model:
            return self.model
        return "unknown"

    def prompt_tokens_from_response(self) -> int | None:
        """Return prompt tokens, preferring explicit field then response.usage."""
        if self.prompt_tokens is not None:
            return self.prompt_tokens
        if isinstance(self.response, dict):
            usage = self.response.get("usage") or {}
            return usage.get("prompt_tokens") if isinstance(usage, dict) else None
        return None

    def completion_tokens_from_response(self) -> int | None:
        """Return completion tokens, preferring explicit field then response.usage."""
        if self.completion_tokens is not None:
            return self.completion_tokens
        if isinstance(self.response, dict):
            usage = self.response.get("usage") or {}
            return usage.get("completion_tokens") if isinstance(usage, dict) else None
        return None


# ---------------------------------------------------------------------------
# Tool call events  (type = "ToolCall")
# ---------------------------------------------------------------------------


class ToolCallPayload(_AutogenEventBase):
    """Payload for ToolCall events."""

    type: Literal["ToolCall"]
    tool_name: str | None = None


# ---------------------------------------------------------------------------
# Exception events  (type = "MessageHandlerException" | "AgentConstructionException")
# ---------------------------------------------------------------------------


class ExceptionPayload(_AutogenEventBase):
    """Payload for exception events."""

    type: Literal["MessageHandlerException", "AgentConstructionException"]
    handling_agent: str | None = None
    error_type: str | None = None
    exception: str | None = None

    @computed_field  # type: ignore[misc]
    @property
    def component(self) -> str:
        """Resolve the responsible component name for metrics labelling."""
        return self.handling_agent or self.agent_id or "unknown"


# ---------------------------------------------------------------------------
# Message routing events  (type = "Message")
# ---------------------------------------------------------------------------


class MessageEventPayload(_AutogenEventBase):
    """Payload for Message routing events."""

    type: Literal["Message"]
    sender: str | None = None
    receiver: Any = None
    kind: str | None = None
    delivery_stage: str | None = None
    payload: str | None = None  # JSON string of the inner message

    @computed_field  # type: ignore[misc]
    @property
    def parsed_payload(self) -> dict[str, Any] | None:
        """Decode the inner payload JSON string, or return None on failure."""
        if not isinstance(self.payload, str) or not self.payload:
            return None
        try:
            result = json.loads(self.payload)
            return result if isinstance(result, dict) else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Union + discriminated dispatch
# ---------------------------------------------------------------------------

# Pydantic discriminated union — each model has a unique Literal on `type`.
# ``LLMStreamEnd`` shares the same model class (both are Literal["LLMCall","LLMStreamEnd"])
# so we can't use a discriminator directly.  Instead we use a tagged router.

_TYPE_MAP: dict[str, type[_AutogenEventBase]] = {
    "LLMCall": LLMEventPayload,
    "LLMStreamEnd": LLMEventPayload,
    "ToolCall": ToolCallPayload,
    "MessageHandlerException": ExceptionPayload,
    "AgentConstructionException": ExceptionPayload,
    "Message": MessageEventPayload,
}

# Public type alias for annotating call sites
AutogenEventPayload = Union[
    LLMEventPayload, ToolCallPayload, ExceptionPayload, MessageEventPayload
]


def parse_autogen_event(payload: Any) -> AutogenEventPayload | None:
    """Parse a raw autogen_core.events dict into the appropriate typed model.

    Returns ``None`` for unknown event types or unparseable payloads — callers
    should handle None gracefully (e.g. skip metrics recording).

    Args:
        payload: A ``dict`` extracted from a logging record message, or any
            other value (returns None for non-dict inputs).

    Returns:
        A typed Pydantic model instance, or None.
    """
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    if not isinstance(event_type, str):
        return None
    model_cls = _TYPE_MAP.get(event_type)
    if model_cls is None:
        return None
    try:
        return model_cls.model_validate(payload)
    except Exception:
        logger.debug("AutoGen event parse failed for type=%r", event_type, exc_info=True)
        return None
