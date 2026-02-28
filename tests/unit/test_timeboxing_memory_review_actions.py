from __future__ import annotations

from types import MethodType

import pytest

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.preferences import ConstraintBase, ConstraintNecessity
from fateforger.agents.timeboxing.tool_result_models import MemoryToolResult


class _FakeStore:
    async def query_constraints(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return [
            {
                "uid": "uid-active",
                "constraint_record": {
                    "name": "Protect mornings",
                    "description": "No meetings before 11:00",
                    "status": "locked",
                    "scope": "profile",
                    "source": "user",
                },
            },
            {
                "uid": "uid-idle",
                "constraint_record": {
                    "name": "Gym evening",
                    "description": "Workout after work",
                    "status": "proposed",
                    "scope": "session",
                    "source": "system",
                },
            },
        ]


@pytest.mark.asyncio
async def test_memory_list_marks_used_this_session() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.active_constraints = []
    session.suppressed_durable_uids = set()
    store = _FakeStore()
    memory_ops: list[str] = []
    captured: dict[str, MemoryToolResult] = {}

    async def _collect_constraints(_self, _session):  # noqa: ANN001
        return [
            ConstraintBase(
                name="Protect mornings",
                description="No meetings before 11:00",
                necessity=ConstraintNecessity.SHOULD,
                hints={"uid": "uid-active"},
            )
        ]

    def _record(_self, *, session, result):  # noqa: ANN001
        _ = session
        captured["result"] = result
        return result.to_tool_payload()

    agent._ensure_durable_constraint_store = lambda: store
    agent._append_background_update_once = lambda *_args, **_kwargs: None
    agent._collect_constraints = MethodType(_collect_constraints, agent)
    agent._record_memory_tool_result = MethodType(_record, agent)

    payload = await TimeboxingFlowAgent._run_memory_tool_action(
        agent,
        action="list",
        session=session,
        memory_operations=memory_ops,
        memory_request_text="which memories are active?",
        text_query=None,
        statuses=None,
        scopes=None,
        necessities=None,
        tags=None,
        limit=20,
    )

    assert payload["ok"] is True
    assert memory_ops == ["list:2"]
    by_uid = {item["uid"]: item for item in payload["constraints"]}
    assert by_uid["uid-active"]["used_this_session"] is True
    assert by_uid["uid-idle"]["used_this_session"] is False
    assert captured["result"].count == 2


@pytest.mark.asyncio
async def test_memory_action_guarded_returns_structured_error_on_backend_failure() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.active_constraints = []
    session.suppressed_durable_uids = set()
    memory_ops: list[str] = []
    updates: list[str] = []

    async def _boom(_self, **kwargs):  # noqa: ANN001, ANN003
        _ = kwargs
        raise RuntimeError("constraint-memory tool constraint_query_constraints failed")

    def _record(_self, *, session, result):  # noqa: ANN001
        _ = session
        return result.to_tool_payload()

    agent._run_memory_tool_action = MethodType(_boom, agent)
    agent._append_background_update_once = lambda _session, text: updates.append(text)
    agent._session_debug = lambda *_args, **_kwargs: None
    agent._record_memory_tool_result = MethodType(_record, agent)
    agent._durable_constraint_store = object()
    agent._constraint_memory_unavailable_reason = None

    payload = await TimeboxingFlowAgent._run_memory_tool_action_guarded(
        agent,
        action="list",
        session=session,
        memory_operations=memory_ops,
        memory_request_text="which memories are active?",
        text_query=None,
        statuses=None,
        scopes=None,
        necessities=None,
        tags=None,
        limit=20,
    )

    assert payload["ok"] is False
    assert payload["action"] == "list"
    assert "unavailable" in payload.get("message", "").lower()
    assert agent._durable_constraint_store is None
    assert "RuntimeError" in (agent._constraint_memory_unavailable_reason or "")
    assert any("memory backend is currently unavailable" in text.lower() for text in updates)
