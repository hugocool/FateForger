"""Common Haunter utilities."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from ..core.logging import get_logger
from ..core.slack import schedule_dm, delete_scheduled


class BaseHaunter(ABC):
    """Base class with Slack and scheduler helpers."""

    def __init__(self, session_id: int, slack: AsyncWebClient, scheduler: AsyncIOScheduler, channel: str) -> None:
        self.session_id = session_id
        self.slack = slack
        self.scheduler = scheduler
        self.channel = channel
        self.logger = get_logger(self.__class__.__name__)

    def schedule_job(self, job_id: str, when: datetime, fn: Callable, *args, **kwargs) -> None:
        self.scheduler.add_job(fn, trigger="date", run_date=when, args=args, kwargs=kwargs, id=job_id, replace_existing=True)

    async def send(self, text: str) -> str:
        resp = await self.slack.chat_postMessage(channel=self.channel, text=text)
        return resp["ts"]

    async def schedule_slack(self, text: str, when: datetime) -> str:
        post_at = int(when.timestamp())
        return await schedule_dm(self.slack, self.channel, text, post_at)

    async def delete_scheduled(self, scheduled_id: str) -> None:
        await delete_scheduled(self.slack, self.channel, scheduled_id)

    @staticmethod
    def next_run_time(attempt: int, base: int = 5, cap: int = 120) -> datetime:
        delay = min(base * (2 ** attempt), cap)
        return datetime.utcnow() + timedelta(minutes=delay)

    @abstractmethod
    async def handle_reply(self, text: str) -> None:
        pass
