"""The bench's host capture is an observer: it never changes a draw's outcome.

`scripts/bench/_interpreter_tier_plugin.py` wraps the OpenAI SDK's call and
reads host, tokens and finish reason off the raw completion. A completion that
arrives in a shape the reader does not expect must leave the draw exactly as it
was: a draw that answered still answers, and a draw that raised still raises
its own exception, not one the capture made. Offline; no model is called.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))

import _interpreter_tier_plugin as plugin  # noqa: E402


class _BareChoice:
    """A choice with neither ``message`` nor ``finish_reason``."""


class _MissingFields:
    id = "gen-test"
    usage = None
    choices = [_BareChoice()]


class _UnindexableChoices:
    id = "gen-test"
    usage = None
    choices = 5


@pytest.mark.parametrize("completion", [_MissingFields(), _UnindexableChoices()], ids=["missing-attrs", "unindexable"])
async def test_the_capture_never_changes_a_draws_outcome(completion) -> None:
    async def answered(self):  # type: ignore[no-untyped-def]
        return completion

    class _DrawFailed(Exception):
        pass

    original = _DrawFailed("the draw's own failure")
    original.completion = completion  # type: ignore[attr-defined]

    async def raised(self):  # type: ignore[no-untyped-def]
        raise original

    holder: dict = {}
    token = plugin._raw.set(holder)
    try:
        returned = await plugin._wrap_sdk_call(answered)(object())
        assert returned is completion
        assert holder.get("raw_finish_reason") is None

        with pytest.raises(_DrawFailed) as excinfo:
            await plugin._wrap_sdk_call(raised)(object())
        assert excinfo.value is original
    finally:
        plugin._raw.reset(token)
