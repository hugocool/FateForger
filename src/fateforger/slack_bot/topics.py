"""Utilities to bind Slack threads to AutoGen topics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional

from autogen_core import TopicId


@dataclass
class SlackTopicBinding:
    channel: str
    thread_ts: str
    topic_id: TopicId
    intent: str
    agent_type: str
    queue: "asyncio.Queue[dict]" = field(default_factory=asyncio.Queue, kw_only=True)
    drain_task: Optional[asyncio.Task] = field(default=None, kw_only=True)


class TopicRegistry:
    """In-memory registry that maps Slack threads to AutoGen topics."""

    _global: "TopicRegistry | None" = None

    def __init__(self, *, base_type: str = "slack.thread") -> None:
        self._base_type = base_type
        self._bindings: Dict[str, SlackTopicBinding] = {}

    @staticmethod
    def make_key(channel: str, thread_ts: str) -> str:
        return f"{channel}:{thread_ts}"

    @classmethod
    def set_global(cls, registry: "TopicRegistry") -> None:
        cls._global = registry

    @classmethod
    def get_global(cls) -> "TopicRegistry | None":
        return cls._global

    def _binding_by_topic(self, topic_id: TopicId) -> Optional[SlackTopicBinding]:
        for binding in self._bindings.values():
            if binding.topic_id == topic_id:
                return binding
        return None

    def ensure_binding(
        self,
        channel: str,
        thread_ts: str,
        *,
        intent: str,
        agent_type: str,
    ) -> SlackTopicBinding:
        key = self.make_key(channel, thread_ts)
        existing = self._bindings.get(key)
        if existing:
            return existing

        topic = TopicId(type=f"{self._base_type}.{intent}", source=key)
        binding = SlackTopicBinding(
            channel=channel,
            thread_ts=thread_ts,
            topic_id=topic,
            intent=intent,
            agent_type=agent_type,
            queue=asyncio.Queue(),
        )
        self._bindings[key] = binding
        return binding

    def get(self, channel: str, thread_ts: str) -> Optional[SlackTopicBinding]:
        return self._bindings.get(self.make_key(channel, thread_ts))

    def get_by_topic(self, topic_id: TopicId) -> Optional[SlackTopicBinding]:
        return self._binding_by_topic(topic_id)

    def pop(self, channel: str, thread_ts: str) -> Optional[SlackTopicBinding]:
        return self._bindings.pop(self.make_key(channel, thread_ts), None)


__all__ = ["TopicRegistry", "SlackTopicBinding"]
