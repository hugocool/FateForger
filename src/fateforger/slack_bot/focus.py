from __future__ import annotations
from dataclasses import dataclass
from cachetools import TTLCache
from typing import Iterable


@dataclass(frozen=True)
class FocusBinding:
    agent_type: str
    set_by_user: str
    note: str | None = None


class FocusManager:
    """
    Thread-scoped focus mapping: (channel, thread_ts|ts) -> FocusBinding
    Backed by an in-memory TTL cache (ephemeral on restarts).
    """

    def __init__(self, *, ttl_seconds: int, allowed_agents: Iterable[str]):
        self._cache = TTLCache(maxsize=10_000, ttl=ttl_seconds)
        self._allowed = set(allowed_agents)

    @staticmethod
    def thread_key(channel: str, thread_ts: str | None, ts: str) -> str:
        # Use thread_ts if present, else message ts (root message)
        return f"{channel}:{thread_ts or ts}"

    def set_focus(
        self, key: str, agent_type: str, *, by_user: str, note: str | None = None
    ) -> FocusBinding:
        if agent_type not in self._allowed:
            raise ValueError(
                f"Unknown agent '{agent_type}'. Allowed: {sorted(self._allowed)}"
            )
        binding = FocusBinding(agent_type=agent_type, set_by_user=by_user, note=note)
        self._cache[key] = binding
        return binding

    def clear_focus(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None

    def get_focus(self, key: str) -> FocusBinding | None:
        return self._cache.get(key)

    def allowed_agents(self) -> list[str]:
        return sorted(self._allowed)
