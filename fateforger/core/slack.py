from slack_sdk.web.async_client import AsyncWebClient


async def schedule_dm(client: AsyncWebClient, channel: str, text: str, post_at: int, thread_ts: str | None = None) -> str:
    """Schedule a DM via Slack and return scheduled_message_id."""
    resp = await client.chat_scheduleMessage(channel=channel, text=text, post_at=post_at, thread_ts=thread_ts)
    return resp["scheduled_message_id"]


async def delete_scheduled(client: AsyncWebClient, channel: str, scheduled_id: str) -> None:
    """Delete scheduled Slack message."""
    await client.chat_deleteScheduledMessage(channel=channel, scheduled_message_id=scheduled_id)
