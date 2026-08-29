import asyncio
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.context import BoltContext
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.haunt.delivery import set_delivery_sink
from fateforger.slack_bot.haunt_delivery import make_slack_delivery_sink

from ..core.config import settings
from ..core.logging_config import configure_logging

# Pull in your AutoGen runtime initialization
from ..core.runtime import initialize_runtime, shutdown_runtime
from .bootstrap import ensure_workspace_ready
from .focus import FocusManager
from .handlers import register_handlers
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
    slack_client = AsyncWebClient(
        token=settings.slack_bot_token, session=aiohttp_session
    )

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
            "INBOUND type=%s event=%s subtype=%s channel=%s thread_ts=%s user=%s text=%s",
            body.get("type"),
            ev.get("type"),
            ev.get("subtype"),
            ev.get("channel"),
            ev.get("thread_ts"),
            ev.get("user"),
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
            "admonisher_agent",
        ],
    )
    register_handlers(
        app,
        runtime,
        focus,
        default_agent="receptionist_agent",
    )

    # Strict bootstrap: configuration/scope issues should fail loudly at startup.
    engine = create_async_engine(_coerce_async_database_url(settings.database_url))
    await ensure_slack_workspace_schema(engine)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    store = SlackWorkspaceStore(sessionmaker)
    await ensure_workspace_ready(app.client, store=store)

    # catch and print anything bad
    @app.error
    async def on_error(error, body, logger):
        logger.exception("BOLT ERROR: %s\nBODY=%s", error, body)

    return app


logger = logging.getLogger(__name__)


#: How often the stop-flag watcher looks. Short enough that a dev restart feels
#: immediate, long enough that an idle bot is not spinning on the filesystem.
_STOP_POLL_SECONDS = 0.5


async def _await_stop_flag(stop_file: Path) -> None:
    """Return once ``stop_file`` exists, then remove it.

    A cooperative shutdown switch for the development loop. Socket Mode holds
    the connection until the process ends, so stopping the bot otherwise means
    signalling the process — which an operator can do and an automated agent
    working in a sandbox generally cannot. This gives both the same handle, and
    gives the bot a chance to close its Slack connection, its HTTP session and
    its runtime rather than dying mid-turn.

    The file is removed here rather than at startup so that a flag set while the
    bot is down still stops the next one that reads it; clearing it on boot
    would silently swallow the request.
    """

    while True:
        if stop_file.exists():
            logger.info("Stop flag observed at %s; shutting down", stop_file)
            stop_file.unlink(missing_ok=True)
            return
        await asyncio.sleep(_STOP_POLL_SECONDS)


def _stop_file() -> Path | None:
    """Where the stop flag lives, or ``None`` when the switch is disabled."""

    configured = (os.environ.get("FF_BOT_STOP_FILE") or "").strip()
    if not configured:
        return None
    return Path(configured).expanduser()


async def start() -> None:
    app = await build_app()
    handler = AsyncSocketModeHandler(app, settings.slack_app_token, web_client=app.client)
    stop_file = _stop_file()
    logger.info("Starting Socket Mode handler...")
    if stop_file is not None:
        logger.info("Stop flag armed: touch %s to shut down cleanly", stop_file)
    try:
        if stop_file is None:
            await handler.start_async()
        else:
            serving = asyncio.ensure_future(handler.start_async())
            stopping = asyncio.ensure_future(_await_stop_flag(stop_file))
            done, pending = await asyncio.wait(
                {serving, stopping}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # Re-raise whatever ended the wait, so a Socket Mode crash is still
            # a crash rather than an ordinary shutdown wearing its clothes.
            for task in done:
                task.result()
    finally:
        await handler.close_async()
        sess = getattr(app, "_aiohttp_session", None)
        await sess.close()
        await shutdown_runtime()


if __name__ == "__main__":
    asyncio.run(start())
