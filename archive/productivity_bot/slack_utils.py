"""
Slack utilities for scheduling and managing Slack messages.

This module provides utilities for scheduling DMs, deleting scheduled messages,
and managing Slack message delivery in the haunting system.
"""

from typing import Optional

from slack_sdk.web.async_client import AsyncWebClient

from .common import get_logger

__all__ = ["schedule_dm", "delete_scheduled", "send_immediate_dm"]

logger = get_logger("slack_utils")


async def schedule_dm(
    client: AsyncWebClient,
    channel: str,
    text: str,
    post_at: int,
    thread_ts: Optional[str] = None,
) -> str:
    """
    Schedule a Slack DM in a thread; return scheduled_message_id.

    Args:
        client: Slack AsyncWebClient instance
        channel: Slack channel or user ID
        text: Message text to send
        post_at: Unix timestamp when to send the message
        thread_ts: Optional thread timestamp to reply in thread

    Returns:
        str: The scheduled_message_id for later deletion

    Raises:
        SlackApiError: If scheduling fails
    """
    try:
        resp = await client.chat_scheduleMessage(
            channel=channel,
            text=text,
            post_at=post_at,
            thread_ts=thread_ts,
        )

        scheduled_id = resp.get("scheduled_message_id")
        if not scheduled_id:
            raise ValueError("No scheduled_message_id returned from Slack API")

        logger.info(
            f"Scheduled message {scheduled_id} for channel {channel} at {post_at}"
        )
        return scheduled_id

    except Exception as e:
        logger.error(f"Failed to schedule message for {channel}: {e}")
        raise


async def delete_scheduled(
    client: AsyncWebClient, channel: str, scheduled_message_id: str
) -> bool:
    """
    Delete a previously scheduled message.

    Args:
        client: Slack AsyncWebClient instance
        channel: Slack channel where message was scheduled
        scheduled_message_id: ID of the scheduled message to delete

    Returns:
        bool: True if deletion was successful, False otherwise
    """
    try:
        await client.chat_deleteScheduledMessage(
            channel=channel,
            scheduled_message_id=scheduled_message_id,
        )

        logger.info(f"Deleted scheduled message {scheduled_message_id} from {channel}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete scheduled message {scheduled_message_id}: {e}")
        return False


async def send_immediate_dm(
    client: AsyncWebClient,
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
    blocks: Optional[list] = None,
) -> Optional[str]:
    """
    Send an immediate Slack message.

    Args:
        client: Slack AsyncWebClient instance
        channel: Slack channel or user ID
        text: Message text
        thread_ts: Optional thread timestamp to reply in thread
        blocks: Optional Slack blocks for rich formatting

    Returns:
        Optional[str]: Message timestamp if successful, None otherwise
    """
    try:
        response = await client.chat_postMessage(
            channel=channel,
            text=text,
            blocks=blocks,
            thread_ts=thread_ts,
            username="👻 HaunterBot",
            icon_emoji=":ghost:",
        )

        message_ts = response.get("ts")
        logger.info(f"Sent immediate message to {channel}, ts={message_ts}")
        return message_ts

    except Exception as e:
        logger.error(f"Failed to send immediate message to {channel}: {e}")
        return None
