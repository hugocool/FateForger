from __future__ import annotations

from datetime import datetime, timezone
import types

import pytest

pytest.importorskip("autogen_agentchat")

import fateforger.agents.timeboxing.agent as timeboxing_agent_mod
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.nlu import PlannedDateResult


class _FakePlanningDateInterpreter:
    async def on_messages(self, messages, cancellation_token):  # noqa: ARG002
        return object()


@pytest.mark.asyncio
async def test_interpret_planned_date_disables_timeout_dumps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._planning_date_interpreter_agent = _FakePlanningDateInterpreter()

    async def _noop_ensure(_self) -> None:
        return None

    captured: dict[str, object] = {}

    async def _fake_with_timeout(label, awaitable, timeout_s, **kwargs):  # noqa: ANN001
        captured["label"] = label
        captured["timeout_s"] = timeout_s
        captured.update(kwargs)
        return await awaitable

    monkeypatch.setattr(
        agent,
        "_ensure_planning_date_interpreter_agent",
        types.MethodType(_noop_ensure, agent),
    )
    monkeypatch.setattr(timeboxing_agent_mod, "with_timeout", _fake_with_timeout)
    monkeypatch.setattr(
        timeboxing_agent_mod,
        "parse_chat_content",
        lambda _model, _response: PlannedDateResult(planned_date="2026-02-27"),
    )

    out = await TimeboxingFlowAgent._interpret_planned_date(
        agent,
        "today",
        now=datetime(2026, 2, 27, 1, 0, tzinfo=timezone.utc),
        tz_name="Europe/Amsterdam",
    )
    assert out == "2026-02-27"
    assert captured["label"] == "timeboxing:planning-date"
    assert captured["dump_on_timeout"] is False
    assert captured["dump_threads_on_timeout"] is False
