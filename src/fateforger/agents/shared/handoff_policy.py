"""Shared handoff policy helpers for specialist routing."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class HandoffRoute(str, Enum):
    """Deterministic routing result for an assist/handoff request."""

    STAY_CURRENT = "stay_current"
    HANDOFF = "handoff"


class HandoffIntent(BaseModel):
    """Typed handoff intent emitted by an agent decision model."""

    action: str
    target: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class HandoffPolicy(BaseModel):
    """Shared handoff gate that enforces clear intent before routing away."""

    allowed_targets: set[str] = Field(default_factory=set)
    min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    def resolve(self, intent: HandoffIntent) -> HandoffRoute:
        """Return HANDOFF only when target + confidence are explicit and valid."""
        target = (intent.target or "").strip()
        match (
            intent.action == "assist",
            target in self.allowed_targets,
            intent.confidence is not None
            and intent.confidence >= self.min_confidence,
        ):
            case (True, True, True):
                return HandoffRoute.HANDOFF
            case _:
                return HandoffRoute.STAY_CURRENT


__all__ = ["HandoffIntent", "HandoffPolicy", "HandoffRoute"]
