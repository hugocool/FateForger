import pytest
from freezegun import freeze_time
from src.infra import PlanningSession


class TestBootstrapFlow:
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_bootstrap_flow(self, bootstrap_haunter, scheduler):
        with freeze_time("2025-07-18 17:01:00+02:00"):
            await bootstrap_haunter.start()
            await bootstrap_haunter.handle_reply("Tomorrow at 08:00")
        bootstrap_haunter.planner._create_event.assert_awaited_once()
        result = await bootstrap_haunter.db.get(PlanningSession, 1)
        assert result.status.name == "COMPLETE"
        assert not scheduler.get_jobs()
