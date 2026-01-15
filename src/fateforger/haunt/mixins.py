from __future__ import annotations

from typing import Any, Optional

from .models import FollowUpPlan, HauntDirection, HauntEnvelope, HauntTone
from .orchestrator import HauntOrchestrator, HauntTicket


class HauntAwareAgentMixin:
    """Mixin for RoutedAgents that need haunt logging without Slack dependencies."""

    def __init__(
        self,
        *,
        haunt_orchestrator: HauntOrchestrator,
        haunt_agent_id: str,
        default_channel: str = "virtual-thread",
    ) -> None:
        self._haunt = haunt_orchestrator
        self._haunt_agent_id = haunt_agent_id
        self._haunt_channel = default_channel

        self._haunt.register_agent(
            haunt_agent_id,
            callback=self._on_haunt_ticket,
        )

    async def _log_inbound(
        self,
        *,
        session_id: str,
        content: str,
        core_intent: Optional[str] = None,
        tone: HauntTone = HauntTone.NEUTRAL,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        envelope = HauntEnvelope(
            session_id=session_id,
            agent_id=self._haunt_agent_id,
            channel=self._haunt_channel,
            direction=HauntDirection.INBOUND,
            content=content,
            core_intent=core_intent or content,
            tone=tone,
            metadata=metadata or {},
        )
        await self._haunt.record_envelope(envelope)

    async def _log_outbound(
        self,
        *,
        session_id: str,
        content: str,
        core_intent: str,
        follow_up: FollowUpPlan,
        tone: HauntTone = HauntTone.NEUTRAL,
        metadata: Optional[dict[str, Any]] = None,
        message_ref: Optional[str] = None,
    ) -> None:
        envelope = HauntEnvelope(
            session_id=session_id,
            agent_id=self._haunt_agent_id,
            channel=self._haunt_channel,
            direction=HauntDirection.OUTBOUND,
            content=content,
            core_intent=core_intent,
            tone=tone,
            follow_up=follow_up,
            metadata=metadata or {},
            message_ref=message_ref,
        )
        await self._haunt.record_envelope(envelope)

    async def _on_haunt_ticket(self, ticket: HauntTicket) -> None:
        await self.on_haunt_follow_up(ticket)

    async def on_haunt_follow_up(self, ticket: HauntTicket) -> None:
        """Override in subclasses to emit follow-up messages via their channel."""

        # Default behaviour is to log and drop the ticket.
        return None

