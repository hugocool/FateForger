from __future__ import annotations
from dataclasses import dataclass
from cachetools import TTLCache
from typing import Iterable


@dataclass(frozen=True)
class FocusBinding:
    agent_type: str
    set_by_user: str
    note: str | None = None


@dataclass(frozen=True)
class ThreadRedirect:
    target_channel: str
    target_thread_ts: str
    target_key: str
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
        self._redirects = TTLCache(maxsize=10_000, ttl=ttl_seconds)
        self._user_focus = TTLCache(maxsize=10_000, ttl=ttl_seconds)
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

    def set_redirect(
        self,
        origin_key: str,
        *,
        target_channel: str,
        target_thread_ts: str,
        agent_type: str,
        by_user: str,
        note: str | None = None,
    ) -> ThreadRedirect:
        if agent_type not in self._allowed:
            raise ValueError(
                f"Unknown agent '{agent_type}'. Allowed: {sorted(self._allowed)}"
            )
        target_key = f"{target_channel}:{target_thread_ts}"
        redirect = ThreadRedirect(
            target_channel=target_channel,
            target_thread_ts=target_thread_ts,
            target_key=target_key,
            agent_type=agent_type,
            set_by_user=by_user,
            note=note,
        )
        self._redirects[origin_key] = redirect
        return redirect

    def get_redirect(self, origin_key: str) -> ThreadRedirect | None:
        return self._redirects.get(origin_key)

    def clear_redirect(self, origin_key: str) -> bool:
        return self._redirects.pop(origin_key, None) is not None

    def allowed_agents(self) -> list[str]:
        return sorted(self._allowed)

    def set_user_focus(self, user_id: str, agent_type: str) -> None:
        if not user_id:
            return
        if agent_type not in self._allowed:
            return
        self._user_focus[user_id] = agent_type

    def get_user_focus(self, user_id: str) -> str | None:
        return self._user_focus.get(user_id)
