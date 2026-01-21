import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event


class _FakeRuntime:
    def __init__(self, response_text: str):
        self._response = TextMessage(content=response_text, source="timeboxing_agent")
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append(recipient.type)
        return self._response


class _FakeClient:
    def __init__(self):
        self.posted = []
        self.updates = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"channel": payload["channel"], "ts": "p1"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}


async def _unused_say(**_kwargs):
    return {"channel": "C1", "ts": "unused"}


@pytest.mark.asyncio
async def test_slack_updates_include_background_status_text():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    focus.set_focus("C1:111", "timeboxing_agent", by_user="U1")
    runtime = _FakeRuntime(
        "Stage 1/5 (CollectConstraints)\nSummary:\n- ok\nBackground:\n- Syncing"
    )
    client = _FakeClient()

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C1", "user": "U1", "text": "plan", "ts": "111"},
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert runtime.calls == ["timeboxing_agent"]
    assert client.updates
    assert "Background:" in (client.updates[-1].get("text") or "")
