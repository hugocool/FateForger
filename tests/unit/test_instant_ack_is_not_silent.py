"""The first frame a user sees, and the reason it once appeared 61s late.

`instant_ack` exists so a mention is acknowledged before any of the invisible
work behind it -- a registry ensure, an invite, a registration guard observed
timing out at 3s. It returns None on failure so it can never break the turn it
is acknowledging, which is right; it used to do that in silence, which is not.
"""

from __future__ import annotations

import logging

import pytest

from fateforger.slack_bot.handlers import instant_ack


class _Boom:
    """A Slack client whose post fails the way the real one might."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def chat_postMessage(self, **_payload):
        raise self._error


class _Ok:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.calls.append(payload)
        return {"ok": True, "channel": payload["channel"], "ts": "1.0"}


async def test_a_failed_ack_is_reported_not_swallowed(caplog):
    """The defect this file exists for.

    A fresh mention got no acknowledgement for 61 seconds on 2026-09-01. The
    handler ran, this was its first statement, and nothing appeared. Finding
    out why took five eliminations from outside the process because the
    exception went nowhere. One log line replaces all of them.
    """
    caplog.set_level(logging.WARNING, logger="fateforger.slack_bot.handlers")

    result = await instant_ack(
        _Boom(RuntimeError("channel_not_found")), {"channel": "C1", "ts": "1.0"}
    )

    assert result is None, "a failed ack must still not break the turn"
    assert caplog.records, "the failure was swallowed"
    assert "instant_ack failed" in caplog.text
    # The Slack error names the actual problem; losing it is losing the answer.
    assert "channel_not_found" in caplog.text


async def test_an_event_with_no_channel_says_so(caplog):
    """Nothing to post to is a different failure and must read differently."""
    caplog.set_level(logging.WARNING, logger="fateforger.slack_bot.handlers")

    assert await instant_ack(_Ok(), {"ts": "1.0"}) is None
    assert "names no channel" in caplog.text


async def test_a_successful_ack_is_quiet_and_threaded(caplog):
    """A warning on every healthy turn is a warning nobody reads.

    And it threads under the message being acknowledged: a top-level ack reads
    as an unrelated post, while the reply that follows lands in the thread
    anyway, so the two would be separated.
    """
    caplog.set_level(logging.WARNING, logger="fateforger.slack_bot.handlers")
    client = _Ok()

    result = await instant_ack(client, {"channel": "C1", "ts": "1772.5"})

    assert result is not None
    assert client.calls[0]["thread_ts"] == "1772.5"
    assert not caplog.records, f"a healthy ack logged: {caplog.text}"
