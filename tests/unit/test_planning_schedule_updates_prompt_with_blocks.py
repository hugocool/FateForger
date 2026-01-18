import pytest

pytest.importorskip("autogen_agentchat")

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from autogen_core import AgentId

from fateforger.agents.schedular.messages import UpsertCalendarEvent
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.planning import PlanningCoordinator


class _DummyRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient: AgentId):
        self.calls.append((message, recipient))
        return SlackBlockMessage(
            text="Scheduled.",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Scheduled. <https://example.invalid|Open>"},
                }
            ],
        )


class _DummyClient:
    def __init__(self):
        self.updates = []

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

    async def chat_postMessage(self, **payload):
        raise AssertionError("chat_postMessage fallback should not be used in this test")


@pytest.mark.asyncio
async def test_planning_schedule_updates_prompt_with_blocks():
    runtime = _DummyRuntime()
    client = _DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)

    tz = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 1, 18, 9, 0, tzinfo=tz)
    end = start.replace(minute=30)

    await coordinator._schedule_planning_event(
        user_id="U1",
        channel_id="D_DM",
        thread_ts="123.456",
        calendar_id="primary",
        start=start,
        end=end,
        tz=tz,
    )

    assert runtime.calls
    sent, recipient = runtime.calls[-1]
    assert isinstance(sent, UpsertCalendarEvent)
    assert recipient.type == "planner_agent"
    assert recipient.key == "D_DM:123.456"

    # First update is the "Scheduling..." message, second update should include blocks.
    assert len(client.updates) >= 2
    assert client.updates[-1]["channel"] == "D_DM"
    assert client.updates[-1]["ts"] == "123.456"
    assert client.updates[-1].get("blocks")
