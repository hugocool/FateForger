import asyncio
import logging
from typing import Awaitable, Callable

import aiohttp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.context import BoltContext
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..core.config import settings
from ..core.logging_config import configure_logging

# Pull in your AutoGen runtime initialization
from ..core.runtime import initialize_runtime
from .bootstrap import ensure_workspace_ready
from .focus import FocusManager
from .handlers import register_handlers
from fateforger.haunt.delivery import set_delivery_sink
from fateforger.slack_bot.haunt_delivery import make_slack_delivery_sink
from .workspace_store import SlackWorkspaceStore, ensure_slack_workspace_schema

configure_logging(default_level=settings.log_level)


def _coerce_async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


async def build_app() -> AsyncApp:

    runtime = await initialize_runtime()

    # Reuse a single aiohttp session for Slack Web API calls and close it on shutdown.
    aiohttp_session = aiohttp.ClientSession()
    slack_client = AsyncWebClient(token=settings.slack_bot_token, session=aiohttp_session)

    app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
        client=slack_client,
    )
    # Stash for cleanup in `start()`.
    setattr(app, "_aiohttp_session", aiohttp_session)

    # Wire user-facing delivery (haunting/planning reminders) into Slack.
    set_delivery_sink(make_slack_delivery_sink(app.client))

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

    focus = FocusManager(
        ttl_seconds=settings.slack_focus_ttl_seconds,
        allowed_agents=[
            "receptionist_agent",
            "planner_agent",
            "timeboxing_agent",
            "revisor_agent",
            "tasks_agent",
        ],
    )
    register_handlers(
        app,
        runtime,
        focus,
        default_agent="receptionist_agent",
    )

    # Best-effort workspace bootstrap (channels + IDs). Requires Slack scopes.
    try:
        store = None
        if settings.database_url:
            engine = create_async_engine(
                _coerce_async_database_url(settings.database_url)
            )
            await ensure_slack_workspace_schema(engine)
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            store = SlackWorkspaceStore(sessionmaker)
        await ensure_workspace_ready(app.client, store=store)
    except SlackApiError as e:
        resp = getattr(e, "response", None)
        needed = None
        provided = None
        if resp is not None:
            try:
                needed = resp.get("needed")
                provided = resp.get("provided")
            except Exception:
                pass
        logging.warning(
            "Workspace bootstrap skipped due to Slack missing scopes (needed=%s provided=%s). "
            "Update Slack app scopes and reinstall.",
            needed,
            provided,
        )
    except Exception:
        logging.exception("Workspace bootstrap failed")

    # catch and print anything bad
    @app.error
    async def on_error(error, body, logger):
        logger.exception("BOLT ERROR: %s\nBODY=%s", error, body)

    return app


async def start() -> None:
    app = await build_app()

    if settings.slack_socket_mode and settings.slack_app_token:
        handler = AsyncSocketModeHandler(app, settings.slack_app_token, web_client=app.client)
        try:
            await handler.start_async()
        finally:
            # Ensure aiohttp sessions are closed on dev reload / Ctrl+C, otherwise you'll see:
            # "ERROR:asyncio:Unclosed client session"
            try:
                await handler.client.close()
            except Exception:
                pass
            sess = getattr(app, "_aiohttp_session", None)
            if sess:
                try:
                    await sess.close()
                except Exception:
                    pass
    else:
        # Fallback to HTTP server
        app.start(port=settings.slack_port)


if __name__ == "__main__":
    asyncio.run(start())
