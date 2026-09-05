"""No write path reaches Hugo's corpus by defaulting to it (#288).

`MEMORY_DB_PATH` defaulted to the relative `data/memory.db` in both write
paths, so anything started without the demo supervisor -- a headless preview,
a script, a test run from the repo root -- wrote into the production store.
That is how the 2026-09-03 placeholder observation landed there.

The same default caused the opposite failure the same day: `demo.py restart`
from a worktree resolved the relative path against a different cwd and served
an *empty* store. One default, two silent wrong answers, which is why it is
replaced by a refusal rather than by a different default.

`demo.py` sets the variable explicitly, so the supervised path is unchanged.
"""
from __future__ import annotations

import pytest


def test_the_memory_server_refuses_to_start_without_an_explicit_store(monkeypatch):
    from memory.mcp_server import resolve_db_path

    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)

    with pytest.raises(RuntimeError, match="MEMORY_DB_PATH"):
        resolve_db_path()


def test_an_explicit_store_is_used_as_given(monkeypatch):
    from memory.mcp_server import resolve_db_path

    monkeypatch.setenv("MEMORY_DB_PATH", "/tmp/scratch.db")

    assert resolve_db_path() == "/tmp/scratch.db"


def test_a_blank_setting_is_refused_like_an_absent_one(monkeypatch):
    """An empty string is how a misconfigured launcher spells "unset"."""
    from memory.mcp_server import resolve_db_path

    monkeypatch.setenv("MEMORY_DB_PATH", "   ")

    with pytest.raises(RuntimeError, match="MEMORY_DB_PATH"):
        resolve_db_path()


def test_the_slack_thread_memory_refuses_the_same_way(monkeypatch):
    """The other write path, which is the one that ran on 2026-09-03."""
    import fateforger.slack_bot.thread_memory as tm

    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)
    monkeypatch.setattr(tm, "_SESSION", None, raising=False)
    monkeypatch.setattr(tm, "_SESSION_FAILED", None, raising=False)

    with pytest.raises(RuntimeError, match="MEMORY_DB_PATH"):
        tm._thread_session()
