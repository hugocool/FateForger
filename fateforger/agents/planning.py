from __future__ import annotations

import httpx

from ..actions.haunt_payload import HauntPayload


class PlanningAgent:
    """Simple planning agent calling MCP endpoints via HTTPX."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def handle_router_message(self, payload: HauntPayload) -> None:
        if payload.action == "create_event":
            await self._create_event(payload)
        elif payload.action == "postpone":
            await self._postpone_event(payload)
        elif payload.action == "mark_done":
            await self._mark_done(payload)

    async def _create_event(self, payload: HauntPayload) -> None:
        await self.client.post("/mcp/create_event", json=payload.model_dump())

    async def _postpone_event(self, payload: HauntPayload) -> None:
        await self.client.post("/mcp/postpone", json=payload.model_dump())

    async def _mark_done(self, payload: HauntPayload) -> None:
        await self.client.post("/mcp/mark_done", json=payload.model_dump())
