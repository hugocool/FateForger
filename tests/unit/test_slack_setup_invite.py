import pytest

pytest.importorskip("slack_bolt")

from fateforger.slack_bot.handlers import _invite_user_to_channels_best_effort


class DummyClient:
    def __init__(self):
        self.invites = []

    async def conversations_invite(self, *, channel: str, users):
        self.invites.append((channel, tuple(users)))


@pytest.mark.asyncio
async def test_invite_user_to_channels_best_effort_invites_each_channel():
    client = DummyClient()
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

