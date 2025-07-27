from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseHaunter


class IncompletePlanningHaunter(BaseHaunter):
    """Stub incomplete haunter."""

    def __init__(self, session_id: int, slack: AsyncWebClient, scheduler: AsyncIOScheduler, db: AsyncSession, channel: str = "D123") -> None:
        super().__init__(session_id, slack, scheduler, channel)
        self.db = db

    async def handle_reply(self, text: str) -> None:
        pass
