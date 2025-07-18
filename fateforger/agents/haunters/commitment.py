from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseHaunter


class CommitmentHaunter(BaseHaunter):
    """Minimal commitment haunter for tests."""

    def __init__(self, session_id: int, slack: AsyncWebClient, scheduler: AsyncIOScheduler, db: AsyncSession, channel: str = "D123") -> None:
        super().__init__(session_id, slack, scheduler, channel)
        self.db = db
        self.scheduled_id: str | None = None

    async def remind(self) -> None:
        when = datetime.utcnow() + timedelta(minutes=2)
        self.scheduled_id = await self.schedule_slack("READY?", when)
        self.schedule_job(f"followup-{self.session_id}", when + timedelta(minutes=1), lambda: None)

    async def handle_reply(self, text: str) -> None:
        if text == "mark_done" and self.scheduled_id:
            await self.delete_scheduled(self.scheduled_id)
            self.scheduler.remove_all_jobs()
