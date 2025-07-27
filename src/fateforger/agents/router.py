from __future__ import annotations

from typing import TYPE_CHECKING
from ..actions.haunt_payload import HauntPayload

if TYPE_CHECKING:
    from autogen_agentchat.agents import AssistantAgent


class RouterAgent:
    """Routes payloads from haunters to the PlanningAgent."""

    def __init__(self, planner: AssistantAgent) -> None:
        self.planner = planner

    async def route(self, payload: HauntPayload) -> None:
        await self.planner.handle_router_message(payload)
