import pytest

from fateforger.infra import PlanningSession, SlackMessage, SessionStatus


class TestModels:
    @pytest.mark.asyncio
    async def test_planning_session_roundtrip(self, db_session):
        session = PlanningSession(status=SessionStatus.IN_PROGRESS)
        db_session.add(session)
        await db_session.flush()
        fetched = await db_session.get(PlanningSession, session.id)
        assert fetched.status == SessionStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_slack_message_roundtrip(self, db_session):
        msg = SlackMessage(ts="1", channel="C", scheduled_id="s1")
        db_session.add(msg)
        await db_session.flush()
        fetched = await db_session.get(SlackMessage, msg.id)
        assert fetched.ts == "1"
