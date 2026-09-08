import pytest

pytest.importorskip("slack_sdk")

from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.haunt.delivery import deliver_user_facing, set_delivery_sink
from fateforger.haunt.messages import UserFacingMessage
from fateforger.slack_bot.haunt_delivery import make_slack_delivery_sink
from tests.doubles.slack import RecordingSlackClient




@pytest.mark.asyncio
async def test_deliver_user_facing_posts_to_explicit_channel_id():
    client = RecordingSlackClient(root_ts="1")
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
    client = RecordingSlackClient(root_ts="1")
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
