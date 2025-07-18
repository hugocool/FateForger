import pytest

from fateforger.actions import HauntPayload
from fateforger.agents.planning import PlanningAgent
from fateforger.agents.router import RouterAgent


class TestRouterAgent:
    @pytest.mark.asyncio
    async def test_route_postpone(self, mocker):
        planner = PlanningAgent(mocker.AsyncMock())
        router = RouterAgent(planner)
        mock = mocker.patch.object(planner, "_postpone_event")
        payload = HauntPayload(session_id=1, action="postpone", minutes=5)
        await router.route(payload)
        mock.assert_awaited_once()
