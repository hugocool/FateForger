from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional, Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from autogen_core import TopicId

from .messages import FollowUpDue, FollowUpSpec
from .settings_store import AdmonishmentSettingsPatch, AdmonishmentSettingsPayload

logger = logging.getLogger(__name__)


@dataclass
class PendingFollowUp:
    message_id: str
    topic_id: str | None
    task_id: str | None
    user_id: str | None
    channel_id: str | None
    content: str
    spec: FollowUpSpec
    attempt: int
    created_at: datetime


class AdmonishmentSettingsStore(Protocol):
    async def get_settings(
        self, *, user_id: str, channel_id: str | None = None
    ) -> Optional[AdmonishmentSettingsPayload]:
        ...

    async def upsert_settings(
        self,
        *,
        user_id: str,
        channel_id: str | None = None,
        patch: AdmonishmentSettingsPatch,
    ) -> AdmonishmentSettingsPayload:
        ...


class HauntingService:
    """Tracks follow-up reminders and schedules due notifications."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        *,
        now: Callable[[], datetime] = datetime.utcnow,
        settings_store: AdmonishmentSettingsStore | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._now = now
        self._settings_store = settings_store
        self._pending: dict[str, PendingFollowUp] = {}
        self._topic_index: dict[str, set[str]] = {}
        self._task_index: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self._on_due: Callable[[FollowUpDue], Awaitable[None]] | None = None

    def set_dispatcher(self, dispatcher: Callable[[FollowUpDue], Awaitable[None]]) -> None:
        self._on_due = dispatcher

    async def schedule_followup(
        self,
        *,
        message_id: str,
        topic_id: TopicId | str | None,
        task_id: str | None,
        user_id: str | None = None,
        channel_id: str | None = None,
        content: str,
        spec: FollowUpSpec,
    ) -> Optional[PendingFollowUp]:
        effective_spec = await self._apply_settings(
            spec, user_id=user_id, channel_id=channel_id
        )
        if not effective_spec:
            return None
        if effective_spec.max_attempts is not None and effective_spec.max_attempts < 1:
            return None
        if not effective_spec.after or effective_spec.after.total_seconds() <= 0:
            return None

        record = PendingFollowUp(
            message_id=message_id,
            topic_id=_topic_key(topic_id),
            task_id=task_id,
            user_id=user_id,
            channel_id=channel_id,
            content=content,
            spec=effective_spec,
            attempt=0,
            created_at=self._now(),
        )

        async with self._lock:
            existing = self._pending.get(message_id)
            if existing:
                await self._remove_record(existing)
            self._store_record(record)
            self._schedule_job(record, record.created_at + effective_spec.after)

        return record

    async def record_user_activity(
        self,
        *,
        topic_id: TopicId | str | None,
        task_id: str | None,
        user_id: str | None = None,
    ) -> int:
        topic_key = _topic_key(topic_id)
        async with self._lock:
            candidates = set()
            if topic_key:
                candidates.update(self._topic_index.get(topic_key, set()))
            if task_id:
                candidates.update(self._task_index.get(task_id, set()))

            cancel_ids = {
                message_id
                for message_id in candidates
                if self._pending.get(message_id)
                and self._pending[message_id].spec.cancel_on_user_reply
            }

        return await self.cancel_followups(message_ids=cancel_ids)

    async def cancel_followups(
        self,
        *,
        message_id: str | None = None,
        topic_id: TopicId | str | None = None,
        task_id: str | None = None,
        message_ids: Optional[set[str]] = None,
    ) -> int:
        topic_key = _topic_key(topic_id)
        async with self._lock:
            ids = set(message_ids or set())
            if message_id:
                ids.add(message_id)
            if topic_key:
                ids.update(self._topic_index.get(topic_key, set()))
            if task_id:
                ids.update(self._task_index.get(task_id, set()))

            records = [self._pending.get(message_id) for message_id in ids]
            to_remove = [record for record in records if record]

            for record in to_remove:
                await self._remove_record(record)

        return len(to_remove)

    async def get_followup(self, message_id: str) -> Optional[PendingFollowUp]:
        async with self._lock:
            return self._pending.get(message_id)

    def _store_record(self, record: PendingFollowUp) -> None:
        self._pending[record.message_id] = record
        if record.topic_id:
            self._topic_index.setdefault(record.topic_id, set()).add(record.message_id)
        if record.task_id:
            self._task_index.setdefault(record.task_id, set()).add(record.message_id)

    async def _remove_record(self, record: PendingFollowUp) -> None:
        self._pending.pop(record.message_id, None)
        if record.topic_id:
            ids = self._topic_index.get(record.topic_id)
            if ids and record.message_id in ids:
                ids.remove(record.message_id)
                if not ids:
                    self._topic_index.pop(record.topic_id, None)
        if record.task_id:
            ids = self._task_index.get(record.task_id)
            if ids and record.message_id in ids:
                ids.remove(record.message_id)
                if not ids:
                    self._task_index.pop(record.task_id, None)
        self._unschedule_job(record.message_id)

    def _schedule_job(self, record: PendingFollowUp, run_at: datetime) -> None:
        self._scheduler.add_job(
            self._dispatch_followup,
            trigger="date",
            run_date=run_at,
            id=self._job_id(record.message_id),
            kwargs={"message_id": record.message_id},
            replace_existing=True,
        )

    def _unschedule_job(self, message_id: str) -> None:
        try:
            self._scheduler.remove_job(self._job_id(message_id))
        except Exception:
            pass

    async def _dispatch_followup(self, message_id: str) -> None:
        async with self._lock:
            record = self._pending.get(message_id)
            if not record:
                return
            due = FollowUpDue(
                message_id=record.message_id,
                topic_id=record.topic_id,
                task_id=record.task_id,
                attempt=record.attempt,
                escalation=record.spec.escalation,
                user_id=record.user_id,
            )

        if self._on_due is None:
            logger.warning("No follow-up dispatcher configured; dropping %s", message_id)
        else:
            try:
                await self._on_due(due)
            except Exception:
                logger.exception("Failed to dispatch follow-up for %s", message_id)

        async with self._lock:
            record = self._pending.get(message_id)
            if not record:
                return

            next_attempt = record.attempt + 1
            max_attempts = record.spec.max_attempts or 1
            if next_attempt >= max_attempts:
                await self._remove_record(record)
                return

            updated = PendingFollowUp(
                message_id=record.message_id,
                topic_id=record.topic_id,
                task_id=record.task_id,
                user_id=record.user_id,
                channel_id=record.channel_id,
                content=record.content,
                spec=record.spec,
                attempt=next_attempt,
                created_at=record.created_at,
            )
            self._pending[message_id] = updated

            if not record.spec.after:
                await self._remove_record(record)
                return
            delay = _next_delay(record.spec.after, next_attempt)
            self._schedule_job(updated, self._now() + delay)

    async def get_settings(
        self, *, user_id: str, channel_id: str | None = None
    ) -> Optional[AdmonishmentSettingsPayload]:
        if not self._settings_store:
            return None
        return await self._settings_store.get_settings(
            user_id=user_id, channel_id=channel_id
        )

    async def upsert_settings(
        self,
        *,
        user_id: str,
        channel_id: str | None = None,
        patch: AdmonishmentSettingsPatch,
    ) -> AdmonishmentSettingsPayload:
        if not self._settings_store:
            raise RuntimeError("Admonishment settings store is not configured")
        return await self._settings_store.upsert_settings(
            user_id=user_id, channel_id=channel_id, patch=patch
        )

    async def _apply_settings(
        self,
        spec: FollowUpSpec,
        *,
        user_id: str | None,
        channel_id: str | None,
    ) -> Optional[FollowUpSpec]:
        if not spec.should_schedule:
            return None

        settings = None
        if self._settings_store and user_id:
            settings = await self._settings_store.get_settings(
                user_id=user_id, channel_id=channel_id
            )

        if settings and not settings.enabled:
            return None

        delay = spec.after
        if delay is None and settings:
            delay = timedelta(minutes=settings.default_delay_minutes)

        if delay is None:
            return None

        max_attempts = spec.max_attempts
        if max_attempts is None:
            max_attempts = settings.max_attempts if settings else 2

        escalation = spec.escalation
        if escalation is None:
            escalation = settings.escalation if settings else "gentle"

        cancel_on_user_reply = spec.cancel_on_user_reply
        if cancel_on_user_reply is None:
            cancel_on_user_reply = settings.cancel_on_user_reply if settings else True

        return FollowUpSpec(
            should_schedule=True,
            after=delay,
            max_attempts=max_attempts,
            escalation=escalation,
            cancel_on_user_reply=cancel_on_user_reply,
        )

    @staticmethod
    def _job_id(message_id: str) -> str:
        return f"haunt-followup::{message_id}"


def _topic_key(topic_id: TopicId | str | None) -> str | None:
    if topic_id is None:
        return None
    return str(topic_id)


def _next_delay(base: timedelta, attempt: int) -> timedelta:
    multiplier = 2 ** max(attempt, 0)
    return base * multiplier


__all__ = ["HauntingService", "PendingFollowUp", "AdmonishmentSettingsStore"]
