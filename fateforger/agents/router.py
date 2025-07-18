from __future__ import annotations

from .planning import PlanningAgent
from ..actions.haunt_payload import HauntPayload


class RouterAgent:
    """Routes payloads from haunters to the PlanningAgent."""

    def __init__(self, planner: PlanningAgent) -> None:
        self.planner = planner

    async def route(self, payload: HauntPayload) -> None:
        await self.planner.handle_router_message(payload)
