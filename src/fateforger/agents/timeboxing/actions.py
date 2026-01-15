"""Timebox action summaries for agent memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class TimeboxAction:
    kind: Literal["insert", "delete", "move", "update"]
    event_key: str
    summary: str
    from_time: Optional[str] = None
    to_time: Optional[str] = None
    reason: Optional[str] = None


__all__ = ["TimeboxAction"]
