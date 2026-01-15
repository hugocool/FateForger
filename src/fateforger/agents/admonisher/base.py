"""Common Haunter utilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.core.logging import get_logger
from fateforger.core.slack import delete_scheduled, schedule_dm

from ...haunt.models import FollowUpPlan, HauntDirection, HauntEnvelope, HauntTone
from ...haunt.orchestrator import HauntOrchestrator, HauntTicket


class BaseHaunter(ABC):
    """Base class with Slack and scheduler helpers."""

    def __init__(
        self,
        session_id: int,
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
        channel: str,
        *,
        orchestrator: Optional[HauntOrchestrator] = None,
        haunt_agent_id: Optional[str] = None,
        backoff_base_minutes: int = 5,
        backoff_cap_minutes: int = 120,
    ) -> None:
        self.session_id = session_id
        self.slack = slack
        self.scheduler = scheduler
        self.channel = channel
        self.logger = get_logger(self.__class__.__name__)
        self.haunt = orchestrator
        self.haunt_agent_id = haunt_agent_id or f"{self.__class__.__name__}:{session_id}"

        if self.haunt:
            self.haunt.register_agent(
                self.haunt_agent_id,
                callback=self._handle_ticket,
                backoff_base_minutes=backoff_base_minutes,
                backoff_cap_minutes=backoff_cap_minutes,
            )

    def schedule_job(
        self, job_id: str, when: datetime, fn: Callable, *args, **kwargs
    ) -> None:
        self.scheduler.add_job(
            fn,
            trigger="date",
            run_date=when,
            args=args,
            kwargs=kwargs,
            id=job_id,
            replace_existing=True,
        )

    async def send(self, text: str) -> str:
        resp = await self.slack.chat_postMessage(channel=self.channel, text=text)
        await self._log_outbound(
            content=text,
            core_intent=text,
            follow_up=FollowUpPlan(required=False),
            tone=HauntTone.NEUTRAL,
            metadata={},
            message_ref=resp["ts"],
        )
        return resp["ts"]

    async def schedule_slack(self, text: str, when: datetime) -> str:
        post_at = int(when.timestamp())
        scheduled_id = await schedule_dm(self.slack, self.channel, text, post_at)
        delay_seconds = max(int((when - datetime.utcnow()).total_seconds()), 0)
        delay_minutes = max((delay_seconds + 59) // 60, 1)

        await self._log_outbound(
            content=text,
            core_intent=text,
            follow_up=FollowUpPlan(required=True, delay_minutes=delay_minutes),
            tone=HauntTone.NEUTRAL,
            metadata={"scheduled_post_at": post_at},
            message_ref=scheduled_id,
        )
        return scheduled_id

    async def delete_scheduled(self, scheduled_id: str) -> None:
        await delete_scheduled(self.slack, self.channel, scheduled_id)
        if self.haunt:
            await self.haunt.acknowledge(str(self.session_id), self.haunt_agent_id)

    @staticmethod
    def next_run_time(attempt: int, base: int = 5, cap: int = 120) -> datetime:
        delay = min(base * (2**attempt), cap)
        return datetime.utcnow() + timedelta(minutes=delay)

    @staticmethod
    def next_delay(attempt: int, base: int = 5, cap: int = 120) -> int:
        """Return delay in minutes using exponential backoff."""
        return min(base * (2**attempt), cap)

    @abstractmethod
    async def handle_reply(self, text: str) -> None:
        pass

    async def _log_inbound(self, text: str, *, core_intent: Optional[str] = None) -> None:
        if not self.haunt:
            return
        envelope = HauntEnvelope(
            session_id=str(self.session_id),
            agent_id=self.haunt_agent_id,
            channel=self.channel,
            direction=HauntDirection.INBOUND,
            content=text,
            core_intent=core_intent or text,
            tone=HauntTone.NEUTRAL,
        )
        await self.haunt.record_envelope(envelope)

    async def _log_outbound(
        self,
        *,
        content: str,
        core_intent: str,
        follow_up: FollowUpPlan,
        tone: HauntTone,
        metadata: dict,
        message_ref: Optional[str] = None,
    ) -> None:
        if not self.haunt:
            return
        envelope = HauntEnvelope(
            session_id=str(self.session_id),
            agent_id=self.haunt_agent_id,
            channel=self.channel,
            direction=HauntDirection.OUTBOUND,
            content=content,
            core_intent=core_intent,
            tone=tone,
            follow_up=follow_up,
            metadata=metadata,
            message_ref=message_ref,
        )
        await self.haunt.record_envelope(envelope)

    async def _handle_ticket(self, ticket: HauntTicket) -> None:
        await self.handle_follow_up(ticket)

    async def handle_follow_up(self, ticket: HauntTicket) -> None:
        """Derived classes can override to respond to follow-up triggers."""
        self.logger.debug("No follow-up handler implemented for %s", self.haunt_agent_id)
