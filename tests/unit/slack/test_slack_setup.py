"""Workspace setup: bootstrapping the channels, inviting the user into them,
and what the setup command answers.
"""

from __future__ import annotations

import pytest


pytest.importorskip("autogen_agentchat")


# ── bootstrapping the workspace ───────────────────────────────────────────────

from fateforger.slack_bot.bootstrap import ensure_workspace_ready
from fateforger.slack_bot.workspace import WorkspaceRegistry


@pytest.fixture(autouse=True)
def _reset_workspace_registry():
    WorkspaceRegistry.set_global(None)
    yield
    WorkspaceRegistry.set_global(None)


class DummyClient:
    def __init__(self, *, existing=None):
        self._existing = dict(existing or {})
        self.created = []
        self.joined = []
        self.posted = []

    async def auth_test(self):
        return {"team_id": "T1"}

    async def conversations_list(self, **kwargs):
        channels = [{"name": name, "id": cid} for name, cid in self._existing.items()]
        return {"ok": True, "channels": channels, "response_metadata": {"next_cursor": ""}}

    async def conversations_create(self, *, name: str, is_private: bool = False):
        cid = f"C_{name}"
        self._existing[name] = cid
        self.created.append((name, is_private))
        return {"ok": True, "channel": {"id": cid, "name": name}}

    async def conversations_join(self, *, channel: str):
        self.joined.append(channel)
        return {"ok": True, "channel": {"id": channel}}

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"ok": True, "ts": "1", "channel": payload.get("channel")}


@pytest.mark.asyncio
async def test_ensure_workspace_ready_creates_and_joins_required_channels():
    WorkspaceRegistry.set_global(None)  # reset
    client = DummyClient(existing={"general": "C_GENERAL"})

    directory = await ensure_workspace_ready(client, store=None)

    assert directory is not None
    assert directory.team_id == "T1"
    assert directory.channels_by_name["general"] == "C_GENERAL"
    # Required channels created
    assert set(directory.channels_by_name.keys()) >= {"plan-sessions", "review", "task-marshalling", "scheduling", "general"}
    assert ("plan-sessions", False) in client.created
    assert ("review", False) in client.created
    assert ("task-marshalling", False) in client.created
    assert ("scheduling", False) in client.created
    # Bot joins channels (including general)
    assert "C_GENERAL" in client.joined
    assert "C_plan-sessions" in client.joined
    assert "C_review" in client.joined
    assert "C_task-marshalling" in client.joined
    assert "C_scheduling" in client.joined


@pytest.mark.asyncio
async def test_ensure_workspace_ready_sets_workspace_registry_global():
    WorkspaceRegistry.set_global(None)  # reset
    client = DummyClient(existing={"general": "C_GENERAL", "timeboxing": "C_TIMEBOX"})

    directory = await ensure_workspace_ready(client, store=None)
    assert directory is not None

    global_dir = WorkspaceRegistry.get_global()
    assert global_dir is directory
    # Legacy channel name maps to the new canonical key.
    assert global_dir.channel_for_name("timeboxing") == "C_TIMEBOX"


# ── inviting the user ─────────────────────────────────────────────────────────

from fateforger.slack_bot.handlers import _invite_user_to_channels_best_effort
from tests.doubles.slack import RecordingSlackClient




@pytest.mark.asyncio
async def test_invite_user_to_channels_best_effort_invites_each_channel():
    client = RecordingSlackClient()
    await _invite_user_to_channels_best_effort(
        client, user_id="U1", channel_ids=["C1", "C2", ""]
    )
    assert client.invites == [("C1", ("U1",)), ("C2", ("U1",))]


class FlakyClient:
    def __init__(self):
        self.calls = 0

    async def conversations_invite(self, *, channel: str, users):
        self.calls += 1
        raise RuntimeError("blocked")


@pytest.mark.asyncio
async def test_invite_user_to_channels_best_effort_swallows_errors():
    client = FlakyClient()
    await _invite_user_to_channels_best_effort(client, user_id="U1", channel_ids=["C1"])
    assert client.calls == 1


# ── the response ──────────────────────────────────────────────────────────────

from fateforger.slack_bot.handlers import _format_workspace_ready_response, _workspace_ready_blocks
from fateforger.slack_bot.workspace import SlackPersona, WorkspaceDirectory


def test_format_workspace_ready_response_includes_channel_links():
    directory = WorkspaceDirectory(
        team_id="T1",
        channels_by_name={
            "general": "CGEN",
            "plan-sessions": "CTIME",
            "review": "CREV",
            "task-marshalling": "CTASK",
            "scheduling": "CSCHED",
            "admonishments": "CADMON",
        },
        channels_by_agent={},
        personas_by_agent={"timeboxing_agent": SlackPersona(username="Timeboxer")},
    )
    text = _format_workspace_ready_response(directory)
    assert "Workspace ready." in text
    assert "<#CTIME>" in text
    assert "<#CREV>" in text
    assert "join" in text.lower()
    assert "invite" in text.lower()

    blocks = _workspace_ready_blocks(directory)
    urls = []
    for block in blocks:
        if block.get("type") == "actions":
            for elem in block.get("elements") or []:
                url = elem.get("url")
                if url:
                    urls.append(url)
    assert "https://app.slack.com/client/T1/CTIME" in urls
    assert "https://app.slack.com/client/T1/CADMON" in urls
