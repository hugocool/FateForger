import pytest

pytest.importorskip("slack_sdk")

from fateforger.slack_bot.bootstrap import ensure_workspace_ready
from fateforger.slack_bot.workspace import WorkspaceRegistry


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
