import os
import re
import asyncio
import logging
from typing import Awaitable, Callable

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.context.context import BoltContext

from autogen_core import AgentId
from autogen_agentchat.messages import TextMessage


logging.basicConfig(level=logging.INFO)
MENTION_PREFIX = re.compile(r"^<@([A-Z0-9]+)>\s*")

from ..core.config import settings

# Pull in your AutoGen runtime initialization
from ..core.runtime import initialize_runtime


async def build_app() -> AsyncApp:

    runtime = (
        await initialize_runtime()
    )  # your SingleThreadedAgentRuntime with agents registered

    app = AsyncApp(
        token=settings.slack_bot_token, signing_secret=settings.slack_signing_secret
    )

    @app.use
    async def log_everything(
        logger: logging.Logger,
        body: dict,
        context: BoltContext,
        next: Callable[[], Awaitable[None]],
    ) -> None:
        ev = body.get("event", {})
        logger.info(
            "INBOUND type=%s event=%s channel=%s thread_ts=%s text=%s",
            body.get("type"),
            ev.get("type"),
            ev.get("channel"),
            ev.get("thread_ts"),
            (ev.get("text") or "")[:120],
        )
        await next()

    async def route_to_planner(body, say, context):
        ev = body["event"]
        channel = ev["channel"]
        thread_ts = ev.get("thread_ts")
        ts = ev["ts"]
        user = ev.get("user") or "unknown"
        text = ev.get("text", "")

        # strip leading @mention if present
        bot_user_id = context.get("bot_user_id")
        if bot_user_id:
            m = MENTION_PREFIX.match(text)
            if m and m.group(1) == bot_user_id:
                text = text[m.end() :].strip()

        agent_id = AgentId("planner_agent", key=f"{channel}:{thread_ts or ts}")
        result = await runtime.send_message(
            TextMessage(content=text, source=user),
            recipient=agent_id,
        )
        # after: result = await runtime.send_message(...)
        msg = (
            getattr(result, "chat_message", None) or result
        )  # handle Response OR TextMessage
        reply_text = getattr(msg, "content", None) or "(no response)"
        await say(text=reply_text, thread_ts=thread_ts or ts)

    # public channels: only react when mentioned
    @app.event("app_mention")
    async def on_app_mention(body, say, context):
        await route_to_planner(body, say, context)

    # DMs to the app
    @app.event("message")
    async def on_dm(body, say, context):
        ev = body.get("event", {})
        # only direct messages from humans (avoid loops)
        if ev.get("channel_type") == "im" and ev.get("subtype") != "bot_message":
            await route_to_planner(body, say, context)

    # catch and print anything bad
    @app.error
    async def on_error(error, body, logger):
        logger.exception("BOLT ERROR: %s\nBODY=%s", error, body)

    return app


async def start() -> None:
    app = await build_app()

    if settings.slack_socket_mode and settings.slack_app_token:
        handler = AsyncSocketModeHandler(app, settings.slack_app_token)
        await handler.start_async()
    else:
        # Fallback to HTTP server
        app.start(port=settings.slack_port)


if __name__ == "__main__":
    asyncio.run(start())
