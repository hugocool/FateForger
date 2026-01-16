import asyncio
import logging
from typing import Awaitable, Callable

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_bolt.context.context import BoltContext
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..core.config import settings

# Pull in your AutoGen runtime initialization
from ..core.runtime import initialize_runtime
from .bootstrap import ensure_workspace_ready
from .focus import FocusManager
from .handlers import register_handlers
from .workspace_store import SlackWorkspaceStore, ensure_slack_workspace_schema

logging.basicConfig(level=logging.INFO)


async def build_app() -> AsyncApp:

    runtime = await initialize_runtime()

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
        handler = AsyncSocketModeHandler(app, settings.slack_app_token)
        await handler.start_async()
    else:
        # Fallback to HTTP server
        app.start(port=settings.slack_port)


if __name__ == "__main__":
    asyncio.run(start())


def _coerce_async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
