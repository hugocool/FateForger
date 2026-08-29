"""The development stop switch.

Socket Mode holds the connection until the process ends, so stopping the bot
otherwise means signalling the process. An operator can do that; an automated
agent working in a sandbox generally cannot, and killing a process mid-turn
skips the Slack close, the HTTP session close and the runtime shutdown. A file
both can touch gives them the same handle and a clean exit.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fateforger.slack_bot import bot


def test_the_switch_is_off_unless_a_path_is_configured(monkeypatch) -> None:
    """Catches every deployment growing a shutdown file nobody asked for."""

    monkeypatch.delenv("FF_BOT_STOP_FILE", raising=False)
    assert bot._stop_file() is None

    monkeypatch.setenv("FF_BOT_STOP_FILE", "   ")
    assert bot._stop_file() is None

    monkeypatch.setenv("FF_BOT_STOP_FILE", "~/somewhere/bot.stop")
    configured = bot._stop_file()
    assert configured is not None
    assert "~" not in str(configured)


async def test_the_watcher_returns_once_the_flag_appears(tmp_path) -> None:
    """Catches a switch that is armed but never fires."""

    flag = tmp_path / "bot.stop"
    watching = asyncio.ensure_future(bot._await_stop_flag(flag))

    await asyncio.sleep(bot._STOP_POLL_SECONDS * 2)
    assert not watching.done(), "the watcher returned before the flag existed"

    flag.write_text("")
    await asyncio.wait_for(watching, timeout=5)

    # Removed on observation, not at startup: a flag set while the bot is down
    # must still stop the next one that reads it.
    assert not flag.exists()


async def test_a_flag_already_present_stops_the_next_start(tmp_path) -> None:
    """Catches a boot that clears the request it was supposed to obey."""

    flag = tmp_path / "bot.stop"
    flag.write_text("")

    await asyncio.wait_for(bot._await_stop_flag(flag), timeout=5)

    assert not flag.exists()
