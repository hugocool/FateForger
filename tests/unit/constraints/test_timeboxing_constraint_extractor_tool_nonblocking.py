from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

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
