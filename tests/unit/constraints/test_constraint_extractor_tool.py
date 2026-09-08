"""The constraint extractor as a tool: its strict signature, that calling it
never blocks the turn, and the background extraction it kicks off.
"""

from __future__ import annotations

import pytest
import asyncio
import time
from collections.abc import Awaitable
from typing import Any
import types


pytest.importorskip("autogen_agentchat")


# ── the strict signature ──────────────────────────────────────────────────────

from fateforger.agents.timeboxing import agent as timeboxing_agent_mod


class _DummyExtractor:
    def __init__(self, *, model_client, tools):
        self.model_client = model_client
        self.tools = tools

    async def extract_and_upsert_constraint(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_extract_and_upsert_constraint_tool_is_strict(monkeypatch):
    class _FakeMcpTool:
        def __init__(self, *, name: str, payload):
            self.name = name
            self._payload = payload

        async def run_json(self, _args, _cancellation_token):
            return self._payload

    async def _fake_get_constraint_mcp_tools():
        return [
            _FakeMcpTool(name="constraint_query_types", payload=[]),
            _FakeMcpTool(name="constraint_query_constraints", payload=[]),
            _FakeMcpTool(
                name="constraint_upsert_constraint",
                payload={"uid": "constraint-1"},
            ),
            _FakeMcpTool(name="constraint_log_event", payload={"ok": True}),
        ]

    monkeypatch.setattr(
        timeboxing_agent_mod, "get_constraint_mcp_tools", _fake_get_constraint_mcp_tools
    )
    monkeypatch.setattr(
        timeboxing_agent_mod, "NotionConstraintExtractor", _DummyExtractor
    )
    monkeypatch.setattr(
        timeboxing_agent_mod.settings, "notion_timeboxing_parent_page_id", "dummy", raising=False
    )

    agent = timeboxing_agent_mod.TimeboxingFlowAgent.__new__(
        timeboxing_agent_mod.TimeboxingFlowAgent
    )
    agent._constraint_mcp_tools = None
    agent._notion_extractor = None
    agent._constraint_extractor_tool = None
    agent._model_client = object()

    await timeboxing_agent_mod.TimeboxingFlowAgent._ensure_constraint_mcp_tools(agent)

    assert agent._constraint_extractor_tool is not None
    assert agent._constraint_extractor_tool.schema.get("strict") is True


# ── it must not block the turn ────────────────────────────────────────────────

from fateforger.agents.timeboxing import agent as timeboxing_agent_mod


class _SlowExtractor:
    def __init__(self, *, model_client: Any, tools: list[Any]) -> None:
        """Test double that simulates a slow extractor call."""
        self.model_client = model_client
        self.tools = tools

    async def extract_and_upsert_constraint(self, **_kwargs: Any) -> None:
        """Simulate a long-running background upsert."""
        await asyncio.sleep(10)
        return None


@pytest.mark.asyncio
async def test_extract_and_upsert_constraint_tool_is_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensures the durable constraint upsert tool queues work without blocking the stage."""
    created_tasks: list[asyncio.Task] = []
    real_create_task = timeboxing_agent_mod.asyncio.create_task

    def _create_task_and_cancel(coro: Awaitable[Any]) -> asyncio.Task:
        """Capture the created task and cancel it to keep the test fast."""
        task = real_create_task(coro)
        created_tasks.append(task)
        task.cancel()
        return task

    class _FakeMcpTool:
        def __init__(self, *, name: str, payload: Any) -> None:
            self.name = name
            self._payload = payload

        async def run_json(self, _args: Any, _cancellation_token: Any) -> Any:
            return self._payload

    async def _fake_get_constraint_mcp_tools() -> list[Any]:
        return [
            _FakeMcpTool(name="constraint_query_types", payload=[]),
            _FakeMcpTool(name="constraint_query_constraints", payload=[]),
            _FakeMcpTool(
                name="constraint_upsert_constraint",
                payload={"uid": "constraint-1"},
            ),
            _FakeMcpTool(name="constraint_log_event", payload={"ok": True}),
        ]

    monkeypatch.setattr(
        timeboxing_agent_mod, "get_constraint_mcp_tools", _fake_get_constraint_mcp_tools
    )
    monkeypatch.setattr(timeboxing_agent_mod, "NotionConstraintExtractor", _SlowExtractor)
    monkeypatch.setattr(timeboxing_agent_mod.asyncio, "create_task", _create_task_and_cancel)
    monkeypatch.setattr(
        timeboxing_agent_mod.settings,
        "notion_timeboxing_parent_page_id",
        "dummy",
        raising=False,
    )

    agent = timeboxing_agent_mod.TimeboxingFlowAgent.__new__(
        timeboxing_agent_mod.TimeboxingFlowAgent
    )
    agent._constraint_mcp_tools = None
    agent._notion_extractor = None
    agent._constraint_extractor_tool = None
    agent._durable_constraint_task_keys = set()
    agent._durable_constraint_semaphore = asyncio.Semaphore(1)
    agent._model_client = object()

    await timeboxing_agent_mod.TimeboxingFlowAgent._ensure_constraint_mcp_tools(agent)

    tool = agent._constraint_extractor_tool
    assert tool is not None

    start = time.monotonic()
    result = await asyncio.wait_for(
        tool._func(
            planned_date="2026-01-21",
            timezone="Europe/Amsterdam",
            stage_id="CollectConstraints",
            user_utterance="In general, I don't do meetings before 10.",
            triggering_suggestion="",
            impacted_event_types=["M"],
            suggested_tags=["work_window"],
            decision_scope="",
        ),
        timeout=0.5,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 0.5
    assert isinstance(result, dict)
    assert result.get("queued") is True

    if created_tasks:
        await asyncio.gather(*created_tasks, return_exceptions=True)


# ── extraction in the background ──────────────────────────────────────────────

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nlu import ConstraintInterpretation
from fateforger.agents.timeboxing.preferences import (
    ConstraintBase,
    ConstraintNecessity,
    ConstraintScope,
)


@pytest.mark.asyncio
async def test_queue_constraint_extraction_runs_in_background():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_extraction_tasks = {}
    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)

    async def _fake_interpret(_self, _session, *, text: str, is_initial: bool):
        assert text
        assert is_initial is False
        return ConstraintInterpretation(
            should_extract=True,
            scope="session",
            constraints=[
                ConstraintBase(
                    name="Deep work mornings",
                    description="Do deep work in the mornings.",
                    necessity=ConstraintNecessity.SHOULD,
                    scope=ConstraintScope.SESSION,
                )
            ],
        )

    class _Store:
        async def add_constraints(self, **_kwargs):
            return []

    async def _fake_collect_constraints(_session: Session):
        return []

    agent._interpret_constraints = types.MethodType(_fake_interpret, agent)  # type: ignore[assignment]
    async def _noop_store() -> None:
        return None

    agent._ensure_constraint_store = _noop_store  # type: ignore[assignment]
    agent._constraint_store = _Store()
    agent._collect_constraints = _fake_collect_constraints  # type: ignore[assignment]

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
    )

    task = agent._queue_constraint_extraction(
        session=session,
        text="I do deep work in the mornings.",
        reason="test",
        is_initial=False,
    )
    assert task is not None
    res = await asyncio.wait_for(task, timeout=1.0)
    assert res is not None
    assert not session.pending_constraint_extractions


@pytest.mark.asyncio
async def test_queue_constraint_extraction_respects_classifier():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_extraction_tasks = {}
    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)

    async def _fake_interpret(_self, _session, *, text: str, is_initial: bool):
        assert text
        return ConstraintInterpretation(
            should_extract=False,
            scope="session",
            constraints=[],
        )

    class _Store:
        async def add_constraints(self, **_kwargs):
            return []

    async def _fake_collect_constraints(_session: Session):
        return None

    agent._interpret_constraints = types.MethodType(_fake_interpret, agent)  # type: ignore[assignment]
    async def _noop_store() -> None:
        return None

    agent._ensure_constraint_store = _noop_store  # type: ignore[assignment]
    agent._constraint_store = _Store()
    agent._collect_constraints = _fake_collect_constraints  # type: ignore[assignment]

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
    )

    task = agent._queue_constraint_extraction(
        session=session,
        text="Start timeboxing",
        reason="test",
        is_initial=True,
    )
    assert task is not None
    res = await asyncio.wait_for(task, timeout=1.0)
    assert res is None
