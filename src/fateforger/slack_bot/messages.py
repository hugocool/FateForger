"""Slack-specific message payloads for agent responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass(frozen=True)
class SlackBlockMessage:
    text: str
    blocks: List[dict[str, Any]]


__all__ = ["SlackBlockMessage"]
