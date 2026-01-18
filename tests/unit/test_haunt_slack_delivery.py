import pytest

pytest.importorskip("slack_sdk")

from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.haunt.delivery import deliver_user_facing, set_delivery_sink
from fateforger.haunt.messages import UserFacingMessage
from fateforger.slack_bot.haunt_delivery import make_slack_delivery_sink


class DummyClient:
    def __init__(self):
        self.opened = []
        self.posted = []

    async def conversations_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True, "channel": {"id": "D1"}}

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"ok": True, "ts": "1"}


@pytest.mark.asyncio
async def test_deliver_user_facing_posts_to_explicit_channel_id():
    client = DummyClient()
    set_delivery_sink(make_slack_delivery_sink(client))  # type: ignore[arg-type]

    ctx = MessageContext(
        sender=None,
        topic_id=DefaultTopicId(),
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m1",
    )
    await deliver_user_facing(
        UserFacingMessage(content="hello", user_id="U1", channel_id="C1"),
        ctx,
    )

    assert client.opened == []
    assert client.posted and client.posted[0]["channel"] == "C1"
    assert client.posted[0]["username"] == "Admonisher"


@pytest.mark.asyncio
async def test_deliver_user_facing_opens_dm_when_no_channel_id():
    client = DummyClient()
    set_delivery_sink(make_slack_delivery_sink(client))  # type: ignore[arg-type]

    ctx = MessageContext(
        sender=None,
        topic_id=DefaultTopicId(),
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m2",
    )
    await deliver_user_facing(
        UserFacingMessage(content="nudge", user_id="U1", channel_id=None),
        ctx,
    )

    assert client.opened and client.opened[0]["users"] == ["U1"]
    assert client.posted and client.posted[0]["channel"] == "D1"
    assert client.posted[0]["username"] == "Admonisher"
