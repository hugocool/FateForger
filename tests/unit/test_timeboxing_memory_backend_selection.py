from __future__ import annotations

import asyncio
from types import SimpleNamespace

import fateforger.agents.timeboxing.agent as timeboxing_agent_mod
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


def test_ensure_constraint_memory_client_uses_mem0_backend(monkeypatch) -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_memory_client = None
    agent._constraint_memory_unavailable_reason = None

    sentinel = object()
    captured: dict[str, str] = {}

    def _fake_build(*, user_id: str):
        captured["user_id"] = user_id
        return sentinel

    monkeypatch.setattr(
        timeboxing_agent_mod.settings, "mem0_user_id", "user-123", raising=False
    )
    monkeypatch.setattr(
        timeboxing_agent_mod, "build_mem0_client_from_settings", _fake_build
    )

    client = TimeboxingFlowAgent._ensure_constraint_memory_client(agent)

    assert client is sentinel
    assert captured["user_id"] == "user-123"
    assert agent._constraint_memory_client is sentinel


def test_ensure_constraint_memory_client_stops_retrying_after_failure(monkeypatch) -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_memory_client = None
    agent._constraint_memory_unavailable_reason = None

    calls = {"count": 0}

    def _boom(*, user_id: str):
        _ = user_id
        calls["count"] += 1
        raise RuntimeError("mem0 unavailable")

    monkeypatch.setattr(
        timeboxing_agent_mod.settings, "mem0_user_id", "user-123", raising=False
    )
    monkeypatch.setattr(timeboxing_agent_mod, "build_mem0_client_from_settings", _boom)

    first = TimeboxingFlowAgent._ensure_constraint_memory_client(agent)
    second = TimeboxingFlowAgent._ensure_constraint_memory_client(agent)

    assert first is None
    assert second is None
    assert calls["count"] == 1
    assert "RuntimeError" in (agent._constraint_memory_unavailable_reason or "")


def test_queue_durable_constraint_upsert_queues_without_parent_config(
    monkeypatch,
) -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._durable_constraint_task_keys = set()
    agent._durable_constraint_semaphore = asyncio.Semaphore(1)
    agent._durable_constraint_prefetch_tasks = {}
    agent._durable_constraint_prefetch_semaphore = asyncio.Semaphore(1)
    agent._append_background_update_once = lambda *_args, **_kwargs: None
    agent._reset_durable_prefetch_state = lambda *_args, **_kwargs: None
    agent._queue_durable_constraint_prefetch = lambda *_args, **_kwargs: None

    session = Session(
        thread_ts="thread",
        channel_id="channel",
        user_id="user",
        planned_date="2026-02-13",
        stage=TimeboxingStage.REFINE,
        tz_name="UTC",
    )

    called = {"value": False}

    def _fake_create_task(coro):
        called["value"] = True
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(timeboxing_agent_mod.asyncio, "create_task", _fake_create_task)

    TimeboxingFlowAgent._queue_durable_constraint_upsert(
        agent,
        session=session,
        text="I prefer deep work before noon.",
        reason="test",
        decision_scope="profile",
        constraints=[],
    )

    assert called["value"] is True
