# tests/unit/test_agent_journal_wiring.py
"""The agent's patcher and submitter must be journal-wrapped."""
from __future__ import annotations

import inspect

from fateforger.agents.timeboxing import agent as agent_module


def test_agent_module_imports_journaling_decorators():
    src = inspect.getsource(agent_module)
    assert "JournalingPatcher" in src
    assert "JournalingSubmitter" in src


def test_journal_is_optional_and_failure_is_tolerated():
    """A journal that cannot be opened must not stop the agent from starting."""
    src = inspect.getsource(agent_module._build_journal_store)
    assert "except Exception" in src
    assert "return None" in src


def test_journal_store_is_built_without_touching_the_event_loop():
    """The constructor runs inside a live loop; blocking calls would raise there.

    Because the failure path degrades to None, a blocking call would leave the
    journal silently disabled in production while this suite stayed green.
    """
    src = inspect.getsource(agent_module._build_journal_store)
    assert "run_until_complete" not in src
    assert "asyncio.run" not in src
    assert "journal_sessionmaker" in src


async def test_build_journal_store_works_inside_a_running_loop():
    """Exercise the real constraint rather than asserting on source text."""
    agent_module._JOURNAL_STORE = None
    try:
        assert agent_module._build_journal_store() is not None
    finally:
        agent_module._JOURNAL_STORE = None


def test_wrappers_are_skipped_when_journal_unavailable():
    assert agent_module._maybe_journal_patcher(sentinel := object(), None) is sentinel
    assert agent_module._maybe_journal_submitter(sentinel2 := object(), None) is sentinel2
