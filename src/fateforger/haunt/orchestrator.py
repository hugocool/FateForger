from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, Optional, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .models import CalendarHook, FollowUpPlan, HauntDirection, HauntEnvelope, HauntTicket

HauntCallback = Callable[[HauntTicket], Awaitable[None]]


class HauntOrchestrator:
    """Central coordinator that keeps follow-up timers in sync across agents."""

    def __init__(self, scheduler: AsyncIOScheduler) -> None:
        self._scheduler = scheduler
        self._callbacks: Dict[str, HauntCallback] = {}
        self._backoff_defaults: Dict[str, Tuple[int, int]] = {}
        self._tickets: Dict[str, HauntTicket] = {}
        self._session_index: Dict[tuple[str, str], set[str]] = {}
        self._lock = asyncio.Lock()

    def register_agent(
        self,
        agent_id: str,
        *,
        callback: HauntCallback,
        backoff_base_minutes: int = 5,
        backoff_cap_minutes: int = 120,
    ) -> None:
        """Register an agent to receive haunt callbacks with backoff defaults."""

        self._callbacks[agent_id] = callback
        self._backoff_defaults[agent_id] = (backoff_base_minutes, backoff_cap_minutes)

    async def record_envelope(self, envelope: HauntEnvelope) -> Optional[HauntTicket]:
        """Record a message envelope and schedule follow-up when requested."""

        async with self._lock:
            if envelope.direction is HauntDirection.INBOUND:
                await self._ack_session(envelope.session_id, envelope.agent_id)
                return None

            if not envelope.follow_up.required:
                return None

            ticket = self._build_ticket(envelope)
            await self._store_ticket(ticket)
            return ticket

    async def schedule_calendar_hook(self, hook: CalendarHook) -> HauntTicket:
        """Create/replace a timer derived from a calendar event."""

        envelope = HauntEnvelope(
            session_id=hook.session_id,
            agent_id=hook.agent_id,
            channel=hook.metadata.get("channel", hook.agent_id),
            direction=HauntDirection.OUTBOUND,
            content=f"calendar:event:{hook.event_id}",
            core_intent=f"Attend {hook.title}",
            tone=hook.tone,
            follow_up=FollowUpPlan(
                required=True,
                delay_minutes=hook.metadata.get("post_event_delay", 15),
                max_attempts=hook.metadata.get("max_attempts", 1),
            ),
            metadata={**hook.metadata, "calendar_event_id": hook.event_id},
        )

        ticket = HauntTicket(
            ticket_id=self._ticket_id(hook.agent_id, hook.event_id),
            session_id=hook.session_id,
            agent_id=hook.agent_id,
            run_at=hook.start_at,
            attempt=0,
            payload=envelope,
        )

        async with self._lock:
            await self._store_ticket(ticket, replace=True)
        return ticket

    async def acknowledge(self, session_id: str, agent_id: str) -> None:
        """Public helper to acknowledge a session from outside the orchestrator."""

        async with self._lock:
            await self._ack_session(session_id, agent_id)

    async def _store_ticket(self, ticket: HauntTicket, *, replace: bool = False) -> None:
        """Persist ticket in memory and ensure APScheduler knows about it."""

        existing = self._tickets.get(ticket.ticket_id)
        if existing and not replace:
            return

        key = (ticket.session_id, ticket.agent_id)
        if replace and existing:
            await self._remove_ticket(existing)

        self._tickets[ticket.ticket_id] = ticket
        self._session_index.setdefault(key, set()).add(ticket.ticket_id)

        self._scheduler.add_job(
            self._dispatch_ticket,
            trigger="date",
            run_date=ticket.run_at,
            id=ticket.ticket_id,
            kwargs={"ticket_id": ticket.ticket_id},
            replace_existing=True,
        )

    async def _dispatch_ticket(self, ticket_id: str) -> None:
        """APScheduler callback – forwards the ticket to the owning agent."""

        async with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                return

            callback = self._callbacks.get(ticket.agent_id)
            if not callback:
                await self._remove_ticket(ticket)
                return

            await self._remove_ticket(ticket)

        # Execute the callback outside of the orchestrator lock to avoid re-entrancy issues.
        await callback(ticket)

        envelope = ticket.payload.model_copy(update={"attempt": ticket.attempt + 1})
        if (
            envelope.follow_up.required
            and envelope.attempt < envelope.follow_up.max_attempts
        ):
            # Schedule the next follow-up attempt with exponential backoff.
            next_ticket = self._build_ticket(envelope)
            async with self._lock:
                await self._store_ticket(next_ticket)

    async def _remove_ticket(self, ticket: HauntTicket) -> None:
        self._tickets.pop(ticket.ticket_id, None)
        key = (ticket.session_id, ticket.agent_id)
        ids = self._session_index.get(key)
        if ids and ticket.ticket_id in ids:
            ids.remove(ticket.ticket_id)
            if not ids:
                self._session_index.pop(key, None)

        try:
            self._scheduler.remove_job(ticket.ticket_id)
        except Exception:
            pass

    async def _ack_session(self, session_id: str, agent_id: str) -> None:
        key = (session_id, agent_id)
        ticket_ids = list(self._session_index.get(key, set()))
        for ticket_id in ticket_ids:
            ticket = self._tickets.get(ticket_id)
            if ticket:
                await self._remove_ticket(ticket)

    def _build_ticket(self, envelope: HauntEnvelope) -> HauntTicket:
        base, cap = self._backoff_defaults.get(envelope.agent_id, (5, 120))
        delay_minutes = envelope.follow_up.delay_minutes or base
        if envelope.attempt:
            delay_minutes = min(base * (2 ** envelope.attempt), cap)

        if envelope.attempt == 0:
            reference = envelope.created_at
        else:
            reference = datetime.utcnow()

        run_at = reference + timedelta(minutes=delay_minutes)

        return HauntTicket(
            ticket_id=self._ticket_id(envelope.agent_id),
            session_id=envelope.session_id,
            agent_id=envelope.agent_id,
            run_at=run_at,
            attempt=envelope.attempt,
            payload=envelope,
        )

    @staticmethod
    def _ticket_id(agent_id: str, suffix: Optional[str] = None) -> str:
        base = f"haunt::{agent_id}"
        tail = suffix or uuid.uuid4().hex
        return f"{base}::{tail}"
