from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Optional


@dataclass
class TimeboxingActivity:
    user_id: str
    channel_id: str
    thread_ts: str
    state: str
    last_user_at: datetime


class TimeboxingActivityTracker:
    def __init__(
        self,
        *,
        idle_timeout: timedelta = timedelta(minutes=10),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._sessions: Dict[str, TimeboxingActivity] = {}
        self._idle_tasks: Dict[str, asyncio.Task] = {}
        self._on_idle: Optional[Callable[[str], Awaitable[None]]] = None

    def set_on_idle(
        self, callback: Optional[Callable[[str], Awaitable[None]]]
    ) -> None:
        self._on_idle = callback

    def mark_active(self, *, user_id: str, channel_id: str, thread_ts: str) -> None:
        if not user_id:
            return
        now = self._clock()
        self._sessions[user_id] = TimeboxingActivity(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            state="active",
            last_user_at=now,
        )
        self._schedule_idle_check(user_id, expected_ts=now)

    def mark_inactive(self, *, user_id: str) -> None:
        if not user_id:
            return
        self._sessions.pop(user_id, None)
        task = self._idle_tasks.pop(user_id, None)
        if task:
            task.cancel()

    def get_state(self, user_id: str) -> Optional[str]:
        session = self._sessions.get(user_id)
        return session.state if session else None

    def is_active(self, user_id: str) -> bool:
        session = self._sessions.get(user_id)
        return bool(session and session.state == "active")

    def _schedule_idle_check(self, user_id: str, *, expected_ts: datetime) -> None:
        existing = self._idle_tasks.pop(user_id, None)
        if existing:
            existing.cancel()

        async def _idle_watch() -> None:
            try:
                await asyncio.sleep(self._idle_timeout.total_seconds())
            except asyncio.CancelledError:
                return
            session = self._sessions.get(user_id)
            if not session or session.state != "active":
                return
            if session.last_user_at != expected_ts:
                return
            session.state = "unfinished"
            if self._on_idle:
                await self._on_idle(user_id)

        self._idle_tasks[user_id] = asyncio.create_task(_idle_watch())


timeboxing_activity = TimeboxingActivityTracker()


__all__ = ["TimeboxingActivity", "TimeboxingActivityTracker", "timeboxing_activity"]
