from __future__ import annotations
import re
from typing import Callable

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from autogen_core import AgentId
from autogen_agentchat.messages import TextMessage

from .focus import FocusManager

MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")


def _strip_bot_mention(text: str, bot_user_id: str | None) -> str:
    if not bot_user_id:
        return text
    return re.sub(rf"<@{bot_user_id}>\s*", "", text).strip()


def register_handlers(
    app: AsyncApp,
    runtime,
    focus: FocusManager,
    *,
    default_agent: str = "planner_agent",
):
    """
    Registers:
      - /ff-focus            : set focus to an agent for this thread
      - /ff-clear            : clear focus binding for this thread
      - /ff-status           : show current focus / allowed agents
      - App mention handler  : route @mentions via focus→agent
      - DM handler           : route DMs via focus→agent
    """

    # --- Slash Commands ---

    @app.command("/ff-focus")
    async def cmd_focus(ack, body, respond, logger):
        await ack()
        user_id = body["user_id"]
        channel_id = body["channel_id"]
        text = (body.get("text") or "").strip()
        if not text:
            await respond(
                text=f"Usage: `/ff-focus <agent_type> [note]`\nAllowed: `{', '.join(focus.allowed_agents())}`",
                response_type="ephemeral",
            )
            return

        parts = text.split(maxsplit=1)
        agent_type = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else None

        # For slash commands, there’s no thread_ts; bind to the next messages in this channel root
        key = FocusManager.thread_key(
            channel_id,
            thread_ts=None,
            ts=(
                body["container"]["message_ts"]
                if "container" in body and "message_ts" in body["container"]
                else body["trigger_id"]
            ),
        )
        try:
            binding = focus.set_focus(key, agent_type, by_user=user_id, note=note)
            await respond(
                text=f"Focus set to *{binding.agent_type}* for this thread (TTL active). {f'Note: {binding.note}' if binding.note else ''}",
                response_type="ephemeral",
            )
        except ValueError as e:
            await respond(text=str(e), response_type="ephemeral")

    @app.command("/ff-clear")
    async def cmd_clear(ack, body, respond):
        await ack()
        channel_id = body["channel_id"]
        key = FocusManager.thread_key(
            channel_id, thread_ts=None, ts=body.get("trigger_id", "root")
        )
        removed = focus.clear_focus(key)
        msg = (
            "Focus cleared for this thread."
            if removed
            else "No focus was set for this thread."
        )
        await respond(text=msg, response_type="ephemeral")

    @app.command("/ff-status")
    async def cmd_status(ack, body, respond):
        await ack()
        channel_id = body["channel_id"]
        key = FocusManager.thread_key(
            channel_id, thread_ts=None, ts=body.get("trigger_id", "root")
        )
        binding = focus.get_focus(key)
        if binding:
            note = f"\n• note: {binding.note}" if binding.note else ""
            await respond(
                text=f"Focus for this thread: *{binding.agent_type}* (set by <@{binding.set_by_user}>){note}\nAllowed: `{', '.join(focus.allowed_agents())}`",
                response_type="ephemeral",
            )
        else:
            await respond(
                text=f"No focus set for this thread.\nAllowed: `{', '.join(focus.allowed_agents())}`",
                response_type="ephemeral",
            )

    # --- Routing helpers ---

    async def _route_to_agent(
        *,
        client: AsyncWebClient,
        body: dict,
        say: Callable,
        bot_user_id: str | None,
    ):
        event = body["event"]
        channel = event["channel"]
        user = event.get("user") or event.get("bot_id") or "unknown"
        text = event.get("text") or ""
        thread_ts = event.get("thread_ts")
        ts = event["ts"]

        # Build thread key and resolve focus
        key = FocusManager.thread_key(channel, thread_ts, ts)
        binding = focus.get_focus(key)
        agent_type = binding.agent_type if binding else default_agent

        # Clean @mention prefix in public channels
        cleaned_text = _strip_bot_mention(text, bot_user_id)

        agent_id = AgentId(agent_type, key=key)

        # Wrap and send to AutoGen
        msg = TextMessage(content=cleaned_text, source=user)
        result = await runtime.send_message(msg, recipient=agent_id)

        # Guard if agent returned nothing (shouldn’t, but be safe)
        reply_text = (
            getattr(getattr(result, "chat_message", None), "content", None)
            or "(no response)"
        )
        await say(text=reply_text, thread_ts=thread_ts or ts)

    # --- App mention in public channels ---
    @app.event("app_mention")
    async def on_app_mention(body, say, context, client, logger):
        await _route_to_agent(
            client=client, body=body, say=say, bot_user_id=context.get("bot_user_id")
        )

    # --- Direct messages (IM) ---
    @app.event("message")
    async def on_message_events(body, say, context, client, logger):
        event = body.get("event", {})
        if event.get("channel_type") != "im":
            return  # ignore non-DMs; public channels handled by app_mention
        # Ignore bot messages to avoid loops
        if event.get("subtype") == "bot_message":
            return
        await _route_to_agent(
            client=client, body=body, say=say, bot_user_id=context.get("bot_user_id")
        )
