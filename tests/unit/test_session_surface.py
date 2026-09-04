from __future__ import annotations

import pytest

from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.session_surface import SessionSurface, open_session_surface


class _Client:
    def __init__(self):
        self.posted = []
        self.updates = []
        self.invited = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"channel": payload["channel"], "ts": "root.1"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

    async def conversations_invite(self, **payload):
        self.invited.append(payload)
        return {"ok": True}


@pytest.mark.asyncio
async def test_opening_a_surface_posts_a_root_and_claims_it_for_timeboxing() -> None:
    client = _Client()
    focus = FocusManager(
        ttl_seconds=60, allowed_agents=["timeboxing_agent", "receptionist_agent"]
    )

    surface = await open_session_surface(client, focus, user_id="U1", target_channel="C1")

    assert surface == SessionSurface(
        channel_id="C1", root_ts="root.1", session_key="C1:root.1"
    )
    assert client.posted and client.posted[0]["channel"] == "C1"
    assert focus.get_focus("C1:root.1").agent_type == "timeboxing_agent"
    assert focus.get_user_focus("U1") == "timeboxing_agent"
    assert focus.get_thread_label("C1:root.1") is not None


@pytest.mark.asyncio
async def test_an_origin_key_gets_a_redirect_to_the_new_root() -> None:
    client = _Client()
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])

    surface = await open_session_surface(
        client, focus, user_id="U1", target_channel="C1", origin_key="D1:dm"
    )

    redirect = focus.get_redirect("D1:dm")
    assert redirect is not None and redirect.target_key == surface.session_key
    assert focus.get_focus("D1:dm").agent_type == "timeboxing_agent"


@pytest.mark.asyncio
async def test_an_existing_root_is_repurposed_not_duplicated() -> None:
    client = _Client()
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])

    surface = await open_session_surface(
        client,
        focus,
        user_id="U1",
        target_channel="C1",
        existing_root={"channel": "C1", "ts": "ack.9"},
    )

    assert surface.root_ts == "ack.9"
    assert client.posted == []
    assert client.updates and client.updates[0]["ts"] == "ack.9"
