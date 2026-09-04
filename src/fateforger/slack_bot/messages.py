"""Slack-specific message payloads for agent responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

#: What Slack will accept in one message, and what this bot therefore clips to.
#: They live beside the payload they bound rather than in whichever module first
#: needed them: two renderers that each carried their own copy would drift, and
#: the failure is a message Slack rejects wholesale rather than one that reads
#: badly.
SLACK_MAX_TEXT_CHARS = 3900
SLACK_MAX_BLOCK_TEXT_CHARS = 1600
SLACK_MAX_BLOCKS = 40
#: A modal view holds up to 100 blocks (Slack's cap; a message holds 50 and
#: this project stops at 40). The fold is the one surface that uses it.
SLACK_MAX_MODAL_BLOCKS = 100
SLACK_MAX_PAYLOAD_CHARS = 28000


@dataclass(frozen=True)
class SlackBlockMessage:
    text: str
    blocks: List[dict[str, Any]]

@dataclass(frozen=True)
class SlackThreadStateMessage:
    text: str
    blocks: List[dict[str, Any]] | None = None
    thread_state: str | None = None


__all__ = [
    "SLACK_MAX_BLOCKS",
    "SLACK_MAX_BLOCK_TEXT_CHARS",
    "SLACK_MAX_MODAL_BLOCKS",
    "SLACK_MAX_PAYLOAD_CHARS",
    "SLACK_MAX_TEXT_CHARS",
    "SlackBlockMessage",
    "SlackThreadStateMessage",
]
