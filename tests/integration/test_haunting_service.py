import asyncio
from datetime import datetime, timedelta

import pytest

from fateforger.haunt.messages import FollowUpSpec
from fateforger.haunt.service import HauntingService


@pytest.mark.asyncio
async def test_followup_dispatches_once(scheduler):
    dispatched = []

    async def dispatcher(due):
        dispatched.append(due)

    service = HauntingService(scheduler, now=datetime.now)
    service.set_dispatcher(dispatcher)

    await service.schedule_followup(
        message_id="msg-1",
        topic_id="topic-1",
        task_id=None,
        user_id="U123",
        channel_id="C123",
        content="Follow up here",
        spec=FollowUpSpec(
            should_schedule=True,
            after=timedelta(milliseconds=50),
            max_attempts=1,
        ),
    )

    await asyncio.sleep(0.2)

    assert len(dispatched) == 1
    assert dispatched[0].message_id == "msg-1"


@pytest.mark.asyncio
async def test_user_activity_cancels_followups(scheduler):
    service = HauntingService(scheduler, now=datetime.utcnow)

    await service.schedule_followup(
        message_id="msg-2",
        topic_id="topic-2",
        task_id=None,
        user_id="U123",
        channel_id="C123",
        content="Ping again",
        spec=FollowUpSpec(
            should_schedule=True,
            after=timedelta(minutes=5),
            max_attempts=2,
            cancel_on_user_reply=True,
        ),
    )

    cancelled = await service.record_user_activity(
        topic_id="topic-2",
        task_id=None,
        user_id="U123",
    )

    assert cancelled == 1
    assert await service.get_followup("msg-2") is None
