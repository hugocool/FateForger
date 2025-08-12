from __future__ import annotations
import asyncio

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from ..core.config import Settings
from .focus import FocusManager
from .handlers import register_handlers

# Pull in your AutoGen runtime initialization
from ..core.runtime import initialize_runtime

# Agents you want to allow as per-thread focus.
# Ensure these names match the ones you register with the AutoGen runtime.
ALLOWED_AGENTS = [
    "planner_agent",
    "task_marshal",
    "admonisher",
    "revisor",
]


async def build_app() -> AsyncApp:
    settings = Settings()

    runtime = (
        await initialize_runtime()
    )  # your SingleThreadedAgentRuntime with agents registered
    focus = FocusManager(
        ttl_seconds=settings.slack_focus_ttl_seconds, allowed_agents=ALLOWED_AGENTS
    )

    app = AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )

    # Register all listeners
    register_handlers(app, runtime, focus, default_agent="planner_agent")

    # Attach convenience attributes if you want to introspect in tests
    app._ff_runtime = runtime  # type: ignore[attr-defined]
    app._ff_focus = focus  # type: ignore[attr-defined]
    app._ff_settings = settings  # type: ignore[attr-defined]
    return app


async def start():
    app = await build_app()
    settings: Settings = app._ff_settings  # type: ignore[attr-defined]

    if settings.slack_socket_mode and settings.slack_app_token:
        handler = AsyncSocketModeHandler(app, settings.slack_app_token)
        await handler.start_async()
    else:
        # Fallback to HTTP server
        await app.start(port=settings.slack_port)


if __name__ == "__main__":
    asyncio.run(start())
