from __future__ import annotations

import logging

from autogen_core import MessageContext
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.core.logging_config import record_admonishment_event, record_error
from fateforger.haunt.delivery import DeliverySink
from fateforger.haunt.messages import UserFacingMessage
from fateforger.slack_bot.ui import open_link_blocks
from fateforger.slack_bot.workspace import DEFAULT_PERSONAS, WorkspaceRegistry


logger = logging.getLogger(__name__)


def make_slack_delivery_sink(client: AsyncWebClient) -> DeliverySink:
    async def _deliver(message: UserFacingMessage, _ctx: MessageContext) -> None:
        channel_id = (message.channel_id or "").strip()
        user_id = (message.user_id or "").strip()

        directory = WorkspaceRegistry.get_global()
        persona = (
            directory.persona_for_agent("admonisher_agent")
            if directory
            else DEFAULT_PERSONAS.get("admonisher_agent")
        )
        admonishments_channel_id = ""
        if directory:
            admonishments_channel_id = (directory.channel_for_name("admonishments") or "").strip()

        log_permalink: str | None = None
        if admonishments_channel_id:
            try:
                payload = {"channel": admonishments_channel_id, "text": message.content}
                if persona and persona.username:
                    payload["username"] = persona.username
                if persona and persona.icon_emoji:
                    payload["icon_emoji"] = persona.icon_emoji
                if persona and persona.icon_url:
                    payload["icon_url"] = persona.icon_url
                res = await client.chat_postMessage(**payload)
                ts = res.get("ts")
                if ts:
                    perma = await client.chat_getPermalink(channel=admonishments_channel_id, message_ts=ts)
                    log_permalink = perma.get("permalink")
            except Exception:
                logger.debug("Failed to post admonishment log", exc_info=True)

        if not channel_id and user_id:
            try:
                dm = await client.conversations_open(users=[user_id])
                channel_id = (dm.get("channel") or {}).get("id") or ""
            except Exception:
                logger.debug("Failed to open DM for %s", user_id, exc_info=True)
                record_error(component="haunt_delivery", error_type="dm_open_failed")
                record_admonishment_event(
                    component="haunt_delivery", event="admonishment_sent", status="error"
                )
                return

        if not channel_id:
            record_admonishment_event(
                component="haunt_delivery", event="admonishment_sent", status="skipped"
            )
            return

        payload = {"channel": channel_id, "text": message.content}
        if persona and persona.username:
            payload["username"] = persona.username
        if persona and persona.icon_emoji:
            payload["icon_emoji"] = persona.icon_emoji
        if persona and persona.icon_url:
            payload["icon_url"] = persona.icon_url
        if log_permalink:
            payload["blocks"] = open_link_blocks(
                text=message.content,
                url=log_permalink,
                button_text="Open log",
                action_id="ff_open_admonishments_log",
            )
        try:
            await client.chat_postMessage(**payload)
            record_admonishment_event(
                component="haunt_delivery", event="admonishment_sent", status="ok"
            )
        except Exception:
            logger.exception("Failed to deliver admonishment to channel=%s", channel_id)
            record_error(component="haunt_delivery", error_type="post_message_failed")
            record_admonishment_event(
                component="haunt_delivery", event="admonishment_sent", status="error"
            )

    return _deliver


__all__ = ["make_slack_delivery_sink"]
