import pytest

from fateforger.slack_bot import handlers as handlers_mod


@pytest.mark.asyncio
async def test_timebox_command_routes_to_timeboxing_agent(monkeypatch):
    captured = {}

    async def _fake_route_slack_event(
        *, runtime, focus, default_agent, event, bot_user_id, say, client
    ):
        captured["default_agent"] = default_agent
        captured["event"] = event

    monkeypatch.setattr(handlers_mod, "route_slack_event", _fake_route_slack_event)

    responses = []

    async def _respond(**payload):
        responses.append(payload)

    await handlers_mod._handle_timebox_command(
        runtime=object(),
        focus=object(),
        default_agent="receptionist_agent",
        body={"user_id": "U1", "channel_id": "C1", "text": "tomorrow"},
        client=object(),
        respond=_respond,
    )

    assert captured["default_agent"] == "timeboxing_agent"
    assert captured["event"]["text"] == "tomorrow"
    assert responses
    assert responses[0].get("response_type") == "ephemeral"


@pytest.mark.asyncio
async def test_timebox_command_sets_channel_type_for_dm(monkeypatch):
    captured = {}

    async def _fake_route_slack_event(
        *, runtime, focus, default_agent, event, bot_user_id, say, client
    ):
        captured["event"] = event

    monkeypatch.setattr(handlers_mod, "route_slack_event", _fake_route_slack_event)

    await handlers_mod._handle_timebox_command(
        runtime=object(),
        focus=object(),
        default_agent="receptionist_agent",
        body={"user_id": "U1", "channel_id": "D123", "text": "today"},
        client=object(),
        respond=None,
    )

    assert captured["event"]["channel_type"] == "im"
