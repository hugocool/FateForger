import pytest
from httpx import AsyncClient

from fateforger.actions import HauntPayload
from fateforger.agents.planning import PlanningAgent


class TestPlanningAgent:
    @pytest.mark.asyncio
    async def test_handle_router_message(self, httpx_mock):
        client = AsyncClient(base_url="http://testserver")
        agent = PlanningAgent(client)

        httpx_mock.add_response(url="http://testserver/mcp/create_event", json={"ok": True})
        payload = HauntPayload(session_id=1, action="create_event")
        await agent.handle_router_message(payload)
        assert httpx_mock.get_request().url.path == "/mcp/create_event"
        await client.aclose()
