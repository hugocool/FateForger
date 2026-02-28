"""Typed tool result models for channel-agnostic orchestration outputs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class InteractionMode(str, Enum):
    """Supported interaction render targets."""

    TEXT = "text"
    SLACK = "slack"


class MemoryConstraintItem(BaseModel):
    """Constraint row normalized for memory tool responses and UI serialization."""

    uid: str
    name: str = "Constraint"
    description: str = ""
    status: str | None = None
    scope: str | None = None
    necessity: str | None = None
    source: str | None = None
    confidence: float | None = None
    needs_confirmation: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MemoryConstraintItem | None":
        """Build an item from either a query row or a nested `constraint_record` payload."""
        if not isinstance(payload, dict):
            return None
        constraint = payload.get("constraint_record")
        if not isinstance(constraint, dict):
            constraint = payload
        lifecycle = constraint.get("lifecycle") if isinstance(constraint, dict) else {}
        lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
        selector = constraint.get("selector") if isinstance(constraint, dict) else {}
        selector = selector if isinstance(selector, dict) else {}
        hints = constraint.get("hints") if isinstance(constraint, dict) else {}
        hints = hints if isinstance(hints, dict) else {}
        uid = str(
            payload.get("uid")
            or lifecycle.get("uid")
            or hints.get("uid")
            or ""
        ).strip()
        if not uid:
            return None

        confidence_raw = payload.get("confidence", constraint.get("confidence"))
        confidence: float | None = None
        if confidence_raw is not None:
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = None
        needs_confirmation = bool(
            payload.get("needs_confirmation")
            or selector.get("needs_confirmation")
            or hints.get("needs_confirmation")
        )
        if confidence is not None and confidence < 0.7:
            needs_confirmation = True
        return cls(
            uid=uid,
            name=str(payload.get("name") or constraint.get("name") or "Constraint").strip()
            or "Constraint",
            description=str(
                payload.get("description") or constraint.get("description") or ""
            ).strip(),
            status=_as_optional_text(payload.get("status", constraint.get("status"))),
            scope=_as_optional_text(payload.get("scope", constraint.get("scope"))),
            necessity=_as_optional_text(
                payload.get("necessity", constraint.get("necessity"))
            ),
            source=_as_optional_text(payload.get("source", constraint.get("source"))),
            confidence=confidence,
            needs_confirmation=needs_confirmation,
        )


class MemoryToolResult(BaseModel):
    """Typed result envelope emitted by memory CRUD/search tool actions."""

    action: Literal["list", "get", "update", "archive", "supersede"]
    ok: bool
    message: str | None = None
    error: str | None = None
    uid: str | None = None
    count: int | None = None
    constraints: list[MemoryConstraintItem] = Field(default_factory=list)

    def to_tool_payload(self) -> dict[str, Any]:
        """Serialize for LLM tool-return transport."""
        payload = self.model_dump(exclude_none=True)
        payload["constraints"] = [item.model_dump(exclude_none=True) for item in self.constraints]
        return payload


def _as_optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


__all__ = [
    "InteractionMode",
    "MemoryConstraintItem",
    "MemoryToolResult",
]
