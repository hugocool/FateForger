from __future__ import annotations


async def schedule_dm(client, channel: str, text: str, post_at: int) -> str:
    response = await client.chat_scheduleMessage(
        channel=channel,
        text=text,
        post_at=post_at,
    )
    scheduled_id = response.get("scheduled_message_id")
    if not scheduled_id:
        raise RuntimeError("Slack schedule failed: missing scheduled_message_id")
    return scheduled_id


async def delete_scheduled(client, channel: str, scheduled_id: str) -> None:
    await client.chat_deleteScheduledMessage(
        channel=channel,
        scheduled_message_id=scheduled_id,
    )


__all__ = ["schedule_dm", "delete_scheduled"]
