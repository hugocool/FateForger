from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession

from ...haunt.models import CalendarHook, FollowUpPlan, HauntTone
from ...haunt.orchestrator import HauntOrchestrator, HauntTicket
from ..schedular.models.calendar import CalendarEvent
from .base import BaseHaunter


class CommitmentHaunter(BaseHaunter):
    """Minimal commitment haunter for tests."""

    def __init__(
        self,
        session_id: int,
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
        db: AsyncSession,
        orchestrator: HauntOrchestrator,
        channel: str = "D123",
    ) -> None:
        super().__init__(
            session_id,
            slack,
            scheduler,
            channel,
            orchestrator=orchestrator,
            backoff_base_minutes=10,
            backoff_cap_minutes=180,
        )
        self.db = db
        self.scheduled_id: str | None = None
        self._calendar_hooks: dict[str, HauntTicket] = {}

    async def remind(
        self,
        *,
        prompt: str = "Are you still on track for your commitment?",
        delay_minutes: int = 2,
        follow_up: Optional[FollowUpPlan] = None,
    ) -> None:
        """Send an initial nudge and register follow-up expectations."""

        when = datetime.utcnow() + timedelta(minutes=delay_minutes)
        plan = follow_up or FollowUpPlan(required=True, delay_minutes=delay_minutes)
        if plan.required and not follow_up:
            # ensure we respect the exponential backoff base configured for this haunter
            plan = plan.model_copy(update={"delay_minutes": delay_minutes})

        self.scheduled_id = await self.schedule_slack(prompt, when)
        # schedule_slack already registers the envelope with the orchestrator using plan

    async def handle_follow_up(self, ticket: HauntTicket) -> None:
        """Dispatch follow-up pings when the orchestrator fires."""

        payload = ticket.payload
        metadata = payload.metadata
        tone = payload.tone

        if metadata.get("calendar_event_id"):
            title = metadata.get("title", payload.core_intent)
            message = self._format_calendar_follow_up(title, tone)
        else:
            message = self._format_generic_follow_up(payload.core_intent, tone)

        await self.send(message)

    async def handle_reply(self, text: str) -> None:
        await self._log_inbound(text)
        if text == "mark_done" and self.scheduled_id:
            await self.delete_scheduled(self.scheduled_id)
            self.scheduler.remove_all_jobs()

    async def sync_calendar_events(
        self, events: Iterable[CalendarEvent]
    ) -> list[HauntTicket]:
        """Register/update follow-up timers for upcoming calendar commitments."""

        if not self.haunt:
            return []

        tickets: list[HauntTicket] = []
        for event in events:
            start_at = self._event_start(event)
            if not start_at:
                self.logger.debug(
                    "Skipping calendar event %s because it has no start time", event.summary
                )
                continue

            hook = CalendarHook(
                session_id=str(self.session_id),
                agent_id=self.haunt_agent_id,
                event_id=event.eventId or event.summary,
                title=event.summary,
                start_at=start_at,
                end_at=self._event_end(event),
                tone=HauntTone.SUPPORTIVE,
                metadata={
                    "channel": self.channel,
                    "calendar_event_id": event.eventId or event.summary,
                    "title": event.summary,
                    "description": event.description,
                },
            )

            ticket = await self.haunt.schedule_calendar_hook(hook)
            self._calendar_hooks[hook.event_id] = ticket
            tickets.append(ticket)

        return tickets

    @staticmethod
    def _event_start(event: CalendarEvent) -> Optional[datetime]:
        start = getattr(event, "start", None)
        if isinstance(start, datetime):
            return start

        # Fallback: combine date + start_time if available
        date_part = getattr(event, "start", None)
        time_part = getattr(event, "start_time", None)
        if date_part and time_part:
            return datetime.combine(date_part, time_part)
        return None

    @staticmethod
    def _event_end(event: CalendarEvent) -> Optional[datetime]:
        end = getattr(event, "end", None)
        if isinstance(end, datetime):
            return end

        date_part = getattr(event, "end", None)
        time_part = getattr(event, "end_time", None)
        if date_part and time_part:
            return datetime.combine(date_part, time_part)
        return None

    def _format_generic_follow_up(self, core_intent: str, tone: HauntTone) -> str:
        prefix = self._tone_prefix(tone)
        return f"{prefix} Still on it? {core_intent}"

    def _format_calendar_follow_up(self, title: str, tone: HauntTone) -> str:
        prefix = self._tone_prefix(tone)
        return f"{prefix} {title} is starting now — shall we get it rolling?"

    @staticmethod
    def _tone_prefix(tone: HauntTone) -> str:
        return {
            HauntTone.ASSERTIVE: "⚡️",
            HauntTone.ENCOURAGING: "✨",
            HauntTone.SUPPORTIVE: "🤝",
            HauntTone.PLAYFUL: "🎭",
        }.get(tone, "👻")
