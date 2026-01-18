"""Slack-specific message payloads for agent responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass(frozen=True)
class SlackBlockMessage:
    text: str
    blocks: List[dict[str, Any]]

@dataclass(frozen=True)
class SlackThreadStateMessage:
    text: str
    blocks: List[dict[str, Any]] | None = None
    thread_state: str | None = None


__all__ = ["SlackBlockMessage", "SlackThreadStateMessage"]
