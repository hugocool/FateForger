# tests/memory/test_bridged_server.py
"""The write path under a host that cannot sample (#150 revisited, #158).

The chosen harness has a tools-only MCP bridge — no sampling — so
`memory_observe` raises and nothing a session says is ever recorded. That is
correct behaviour and it means the capability Hugo asked for does not exist
under it.

The bridge gives the server its own provider back. It is a compromise and the
tests here are mostly about keeping it one: chosen explicitly, chosen at boot,
never inferred, and never silently substituted for the host.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from memory.mcp_server import build_bridged_server, build_sampling_server

TOOLS = {
    "memory_observe",
    "memory_reproject",
    "memory_split_constraint",
    "memory_resolve_anchors",
    "memory_classify_day",
    "memory_get_active_constraints",
    "memory_get_faded_constraints",
    "memory_get_suspended_constraints",
    "memory_get_session_constraints",
}


def _db() -> str:
    return os.path.join(tempfile.mkdtemp(), "m.db")


async def test_the_bridge_exposes_the_same_verbs_as_the_sampling_server():
    """One server, two transports. A tool present under one host and absent
    under the other would make the surface depend on configuration.

    Async deliberately: calling asyncio.run() from a sync test under
    pytest-asyncio's auto mode closes the loop for every async test that runs
    after it, which surfaces as unrelated RuntimeErrors in other files.
    """
    bridged = {
        t.name
        for t in await build_bridged_server(
            _db(), api_key="test-key", base_url="https://example.invalid/v1"
        ).list_tools()
    }
    hosted = {t.name for t in await build_sampling_server(_db()).list_tools()}

    assert bridged == hosted
    assert TOOLS <= bridged


def _judge_used(build, **kwargs):
    """Which judge the factory actually handed the service."""
    from memory import mcp_server

    captured = {}
    real = mcp_server.MemoryService

    class Spy(real):
        def __init__(self, db_path, judge):
            captured["judge"] = judge
            super().__init__(db_path, judge)

    mcp_server.MemoryService = Spy
    try:
        build(**kwargs)
    finally:
        mcp_server.MemoryService = real
    return captured["judge"]


def test_the_bridge_holds_a_key_and_the_sampling_server_does_not():
    """The whole cost of this compromise, asserted so it cannot be forgotten.

    #150 removed the model and the key so the host's model governs quality.
    This gives that back, and the test exists so the difference is visible
    rather than incidental.
    """
    from memory.openrouter_judge import OpenRouterJudge
    from memory.sampling import SamplingJudge

    bridged = _judge_used(
        build_bridged_server,
        db_path=_db(),
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )
    hosted = _judge_used(build_sampling_server, db_path=_db())

    assert isinstance(bridged, OpenRouterJudge)
    assert isinstance(hosted, SamplingJudge)
    assert bridged._model == "google/gemini-3.6-flash"   # the measured one


def _judge_kind_chosen(monkeypatch):
    """Which factory main() reaches for, without starting a server."""
    from memory import mcp_server

    # These tests are about the judge, not the store, but main() no longer
    # defaults the store to the production corpus (#288), so every caller
    # names one. A scratch path keeps the question here the judge question.
    monkeypatch.setenv("MEMORY_DB_PATH", "/tmp/judge-selection-scratch.db")
    chosen = {}
    monkeypatch.setattr(
        mcp_server, "build_sampling_server",
        lambda db: chosen.setdefault("kind", "host") and None or type(
            "S", (), {"run": lambda self: None}
        )(),
    )
    monkeypatch.setattr(
        mcp_server, "build_bridged_server",
        lambda db, **kw: chosen.setdefault("kind", "openrouter") and None or type(
            "S", (), {"run": lambda self: None}
        )(),
    )
    mcp_server.main()
    return chosen.get("kind")


def test_host_sampling_is_the_default_even_with_a_key_in_the_environment(
    monkeypatch,
):
    """Never inferred from a key being present.

    A server that quietly stops asking its host because OPENROUTER_API_KEY
    happened to be exported is judged by someone nobody chose, and the store
    keeps filling either way.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "leaked-into-the-environment")
    monkeypatch.delenv("MEMORY_JUDGE", raising=False)

    assert _judge_kind_chosen(monkeypatch) == "host"


def test_the_bridge_is_taken_only_when_asked_for_explicitly(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("MEMORY_JUDGE", "openrouter")

    assert _judge_kind_chosen(monkeypatch) == "openrouter"


def test_an_unknown_judge_kind_refuses_to_start(monkeypatch):
    from memory import mcp_server

    monkeypatch.setenv("MEMORY_DB_PATH", "/tmp/judge-selection-scratch.db")
    monkeypatch.setenv("MEMORY_JUDGE", "gemini")
    with pytest.raises(SystemExit, match="not a judge this build knows"):
        mcp_server.main()


def test_the_bridge_refuses_to_start_without_a_key(monkeypatch):
    """Rather than falling back to host sampling.

    Falling back would mean the server judges differently from how it was
    configured, and nothing would say so.
    """
    from memory import mcp_server

    monkeypatch.setenv("MEMORY_DB_PATH", "/tmp/judge-selection-scratch.db")
    monkeypatch.setenv("MEMORY_JUDGE", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="needs OPENROUTER_API_KEY"):
        mcp_server.main()
