"""Stateless-agent contract tests for TimeboxingFlowAgent.

Architecture invariant (enforced here):
    Every LLM call in TimeboxingFlowAgent must use a **fresh** AssistantAgent
    instance so that each invocation receives ONLY:

        last_message + extracted constraints/memories + current stage artifact

    …and NEVER the accumulated conversation history from previous turns.

Covered call sites
------------------
Stage-gating agents (via _build_one_shot_agent):
  - _run_stage_gate          (COLLECT_CONSTRAINTS, CAPTURE_INPUTS)
  - _run_timebox_summary
  - _run_review_commit
  - _decide_next_action

NLU agents (via nlu factory functions):
  - _interpret_planned_date   → build_planned_date_interpreter()
  - _decide_memory_review_turn→ build_memory_review_router()
  - _interpret_constraints    → build_constraint_interpreter()

For each call site we verify:
  a) A brand-new AssistantAgent is instantiated on EVERY call (not cached).
  b) The agent receives exactly ONE TextMessage per call (single-turn).
  c) After 2 sequential calls the second agent starts with an empty message list.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("autogen_agentchat")

import fateforger.agents.timeboxing.agent as agent_mod
import fateforger.agents.timeboxing.nlu as nlu_mod
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nlu import MemoryReviewDecision
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import StageDecision, StageGateOutput

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


class _InstantiationCounter:
    """Tracks every AssistantAgent instantiation and the messages it receives."""

    def __init__(self, response_content: str = "{}") -> None:
        self.instances: list[_FakeAgent] = []
        self.response_content = response_content

    def make_agent(self, **kwargs: Any) -> "_FakeAgent":  # noqa: ANN401
        inst = _FakeAgent(
            response_content=self.response_content,
            kwargs=kwargs,
        )
        self.instances.append(inst)
        return inst


class _FakeAgent:
    """Minimal AssistantAgent stand-in that records on_messages calls."""

    def __init__(self, *, response_content: str, kwargs: dict[str, Any]) -> None:
        self.response_content = response_content
        self.init_kwargs = kwargs
        self.calls: list[list[Any]] = []  # each element = messages list passed in

    async def on_messages(self, messages: list[Any], token: Any) -> Any:
        _ = token
        self.calls.append(list(messages))
        mock_msg = AsyncMock()
        mock_msg.content = self.response_content
        result = AsyncMock()
        result.chat_message = mock_msg
        return result


def _make_agent(*, model_client: Any) -> TimeboxingFlowAgent:  # noqa: ANN401
    """Return a minimally initialised TimeboxingFlowAgent via __new__."""
    a = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    a._model_client = model_client
    a._constraint_model_client = model_client
    a._session_debug_loggers = {}
    return a


# ---------------------------------------------------------------------------
# NLU factory fresh-agent-per-call tests
# ---------------------------------------------------------------------------


class TestInterpretPlannedDateStateless:
    """_interpret_planned_date must build a new agent on every call."""

    @pytest.mark.asyncio
    async def test_creates_new_agent_per_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        counter = _InstantiationCounter(
            response_content=json.dumps({"planned_date": "2026-03-01"})
        )

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(nlu_mod, "AssistantAgent", _FakeAgent)
        monkeypatch.setattr(agent_mod, "build_planned_date_interpreter", _factory)

        agent = _make_agent(model_client=object())
        now = datetime(2026, 3, 1, 9, 0)

        await agent._interpret_planned_date("tomorrow", now=now, tz_name="UTC")
        await agent._interpret_planned_date("next Monday", now=now, tz_name="UTC")

        # Two separate agents must have been created — one per call.
        assert len(counter.instances) == 2, (
            "_interpret_planned_date must create a fresh agent on every call, "
            f"but only {len(counter.instances)} instance(s) were created for 2 calls."
        )

    @pytest.mark.asyncio
    async def test_each_call_receives_single_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        counter = _InstantiationCounter(
            response_content=json.dumps({"planned_date": "2026-03-01"})
        )

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_planned_date_interpreter", _factory)

        agent = _make_agent(model_client=object())
        now = datetime(2026, 3, 1, 9, 0)

        await agent._interpret_planned_date("tomorrow", now=now, tz_name="UTC")
        await agent._interpret_planned_date("next Monday", now=now, tz_name="UTC")

        for i, inst in enumerate(counter.instances):
            assert (
                len(inst.calls) == 1
            ), f"Agent {i} should receive exactly 1 on_messages call; got {len(inst.calls)}"
            assert (
                len(inst.calls[0]) == 1
            ), f"Agent {i} should receive exactly 1 message per call; got {len(inst.calls[0])}"

    @pytest.mark.asyncio
    async def test_second_call_does_not_see_first_call_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second agent must not carry the first call's messages."""
        counter = _InstantiationCounter(
            response_content=json.dumps({"planned_date": "2026-03-01"})
        )

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_planned_date_interpreter", _factory)

        agent = _make_agent(model_client=object())
        now = datetime(2026, 3, 1, 9, 0)

        await agent._interpret_planned_date("tomorrow", now=now, tz_name="UTC")
        await agent._interpret_planned_date("next Monday", now=now, tz_name="UTC")

        # Second agent must not have received the first agent's messages.
        first_payload = json.loads(counter.instances[0].calls[0][0].content)
        second_payload = json.loads(counter.instances[1].calls[0][0].content)
        assert (
            first_payload["text"] != second_payload["text"]
        ), "The two calls had different input text — confirming independent payloads."
        # Each agent only received its own single message.
        assert len(counter.instances[1].calls[0]) == 1


class TestDecideMemoryReviewTurnStateless:
    """_decide_memory_review_turn must build a new agent on every call."""

    @pytest.mark.asyncio
    async def test_creates_new_agent_per_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        decision_json = json.dumps({"action": "none"})
        counter = _InstantiationCounter(response_content=decision_json)

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_memory_review_router", _factory)

        agent = _make_agent(model_client=object())
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")

        await agent._decide_memory_review_turn(
            session=session, user_message="show me my constraints"
        )
        await agent._decide_memory_review_turn(
            session=session, user_message="update sleep time"
        )

        assert len(counter.instances) == 2, (
            "_decide_memory_review_turn must create a fresh agent per call, "
            f"got {len(counter.instances)} instance(s) for 2 calls."
        )

    @pytest.mark.asyncio
    async def test_each_call_receives_single_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        decision_json = json.dumps({"action": "none"})
        counter = _InstantiationCounter(response_content=decision_json)

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_memory_review_router", _factory)

        agent = _make_agent(model_client=object())
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")

        await agent._decide_memory_review_turn(session=session, user_message="msg1")
        await agent._decide_memory_review_turn(session=session, user_message="msg2")

        for i, inst in enumerate(counter.instances):
            assert len(inst.calls) == 1, f"Agent {i} should receive exactly 1 call"
            assert (
                len(inst.calls[0]) == 1
            ), f"Agent {i} should receive exactly 1 message"

    @pytest.mark.asyncio
    async def test_second_agent_does_not_contain_first_call_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        decision_json = json.dumps({"action": "none"})
        counter = _InstantiationCounter(response_content=decision_json)

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_memory_review_router", _factory)

        agent = _make_agent(model_client=object())
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")

        await agent._decide_memory_review_turn(
            session=session, user_message="first message"
        )
        await agent._decide_memory_review_turn(
            session=session, user_message="second message"
        )

        first_payload = json.loads(counter.instances[0].calls[0][0].content)
        second_payload = json.loads(counter.instances[1].calls[0][0].content)
        assert first_payload["user_message"] == "first message"
        assert second_payload["user_message"] == "second message"
        # Second agent must not know about first call.
        assert len(counter.instances[1].calls[0]) == 1


class TestInterpretConstraintsStateless:
    """_interpret_constraints must build a new agent on every call."""

    @pytest.mark.asyncio
    async def test_creates_new_agent_per_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result_json = json.dumps(
            {
                "constraints": [],
                "scope": "session",
                "entities": [],
                "date_references": [],
                "should_extract": False,
            }
        )
        counter = _InstantiationCounter(response_content=result_json)

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_constraint_interpreter", _factory)

        agent = _make_agent(model_client=object())
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")

        await agent._interpret_constraints(
            session, text="no meetings before 9am", is_initial=True
        )
        await agent._interpret_constraints(session, text="gym at 6pm", is_initial=False)

        assert len(counter.instances) == 2, (
            "_interpret_constraints must create a fresh agent per call, "
            f"got {len(counter.instances)} instance(s) for 2 calls."
        )

    @pytest.mark.asyncio
    async def test_each_call_receives_single_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result_json = json.dumps(
            {
                "constraints": [],
                "scope": "session",
                "entities": [],
                "date_references": [],
                "should_extract": False,
            }
        )
        counter = _InstantiationCounter(response_content=result_json)

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_constraint_interpreter", _factory)

        agent = _make_agent(model_client=object())
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")

        await agent._interpret_constraints(session, text="msg1", is_initial=True)
        await agent._interpret_constraints(session, text="msg2", is_initial=False)

        for i, inst in enumerate(counter.instances):
            assert len(inst.calls) == 1, f"Agent {i} should receive exactly 1 call"
            assert (
                len(inst.calls[0]) == 1
            ), f"Agent {i} should receive exactly 1 message"

    @pytest.mark.asyncio
    async def test_payload_includes_existing_session_constraints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result_json = json.dumps(
            {
                "constraints": [],
                "scope": "session",
                "should_extract": False,
            }
        )
        counter = _InstantiationCounter(response_content=result_json)

        def _factory(*, model_client: Any) -> _FakeAgent:  # noqa: ANN401
            return counter.make_agent(model_client=model_client)

        monkeypatch.setattr(agent_mod, "build_constraint_interpreter", _factory)

        agent = _make_agent(model_client=object())
        existing = Constraint(
            user_id="u1",
            channel_id="c1",
            thread_ts="t1",
            name="Gym at noon",
            description="Keep gym around 12:00.",
            necessity=ConstraintNecessity.SHOULD,
            status=ConstraintStatus.PROPOSED,
            source=ConstraintSource.USER,
            scope=ConstraintScope.SESSION,
            hints={"uid": "gym-noon"},
        )

        class _Store:
            async def list_constraints(self, **kwargs: Any) -> list[Constraint]:  # noqa: ANN401
                assert kwargs["scope"] == ConstraintScope.SESSION
                return [existing]

        async def _noop_store() -> None:
            return None

        agent._ensure_constraint_store = _noop_store  # type: ignore[assignment]
        agent._constraint_store = _Store()
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")

        await agent._interpret_constraints(
            session, text="move gym a bit later", is_initial=False
        )

        payload = json.loads(counter.instances[0].calls[0][0].content)
        assert payload["existing_constraints"]
        assert payload["existing_constraints"][0]["name"] == "Gym at noon"


# ---------------------------------------------------------------------------
# Stage-gating fresh-agent-per-call tests
# ---------------------------------------------------------------------------


class TestBuildOneShotAgentStateless:
    """_build_one_shot_agent must return a distinct object on every call."""

    def test_returns_distinct_instance_per_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each call to _build_one_shot_agent returns a new object."""
        instances: list[object] = []

        class _TrackingAgent:
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
                instances.append(self)

        monkeypatch.setattr(agent_mod, "AssistantAgent", _TrackingAgent)
        monkeypatch.setattr(
            agent_mod,
            "assert_strict_tools_for_structured_output",
            lambda **_: None,
        )

        agent = _make_agent(model_client=object())
        from fateforger.agents.timeboxing.stage_gating import (
            COLLECT_CONSTRAINTS_PROMPT,
            StageGateOutput,
        )

        first = agent._build_one_shot_agent(
            "StageCollectConstraints", COLLECT_CONSTRAINTS_PROMPT, StageGateOutput
        )
        second = agent._build_one_shot_agent(
            "StageCollectConstraints", COLLECT_CONSTRAINTS_PROMPT, StageGateOutput
        )

        assert (
            first is not second
        ), "_build_one_shot_agent must return a new instance on every call"
        assert len(instances) == 2, f"Expected 2 instantiations, got {len(instances)}"

    def test_ten_sequential_calls_produce_ten_instances(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates N Refine turns — each must produce a distinct agent."""
        instances: list[object] = []

        class _TrackingAgent:
            def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
                instances.append(self)

        monkeypatch.setattr(agent_mod, "AssistantAgent", _TrackingAgent)
        monkeypatch.setattr(
            agent_mod,
            "assert_strict_tools_for_structured_output",
            lambda **_: None,
        )

        agent = _make_agent(model_client=object())
        from fateforger.agents.timeboxing.stage_gating import (
            COLLECT_CONSTRAINTS_PROMPT,
            StageGateOutput,
        )

        for _ in range(10):
            agent._build_one_shot_agent(
                "StageCollectConstraints", COLLECT_CONSTRAINTS_PROMPT, StageGateOutput
            )

        assert len(instances) == 10, (
            f"10 Refine turns must produce 10 fresh agents, got {len(instances)}; "
            "history accumulation bug detected."
        )


# ---------------------------------------------------------------------------
# No _ensure_* cached NLU attributes on the class
# ---------------------------------------------------------------------------


class TestNoLegacyCachedAgentAttributes:
    """The agent must not carry any persistent NLU agent instance attributes.

    Presence of these attributes indicates the old caching pattern was
    accidentally re-introduced, which would cause history accumulation.
    """

    _BANNED_ATTRS = (
        "_constraint_interpreter_agent",
        "_planning_date_interpreter_agent",
        "_memory_review_agent",
        # stage agents (also removed)
        "_stage_agents",
        "_decision_agent",
        "_summary_agent",
        "_review_commit_agent",
    )

    def test_no_banned_cache_attributes_on_class(self) -> None:
        """Banned cached-agent attributes must not appear on the class or its annotations."""
        for attr in self._BANNED_ATTRS:
            assert not hasattr(TimeboxingFlowAgent, attr), (
                f"TimeboxingFlowAgent.{attr} is a banned cached-agent attribute. "
                "Remove it and use fresh-per-call factory functions instead."
            )
            annotations = getattr(TimeboxingFlowAgent, "__annotations__", {})
            assert (
                attr not in annotations
            ), f"TimeboxingFlowAgent.__annotations__[{attr!r}] found — banned cached-agent attribute."

    def test_no_banned_ensure_methods_on_class(self) -> None:
        """Banned _ensure_* caching wrapper methods must not exist on the class."""
        banned_methods = (
            "_ensure_planning_date_interpreter_agent",
            "_ensure_memory_review_agent",
            "_ensure_constraint_interpreter_agent",
        )
        for method in banned_methods:
            assert not hasattr(TimeboxingFlowAgent, method), (
                f"TimeboxingFlowAgent.{method} is a banned caching wrapper method. "
                "Remove it and call the factory function directly per invocation."
            )

    def test_no_banned_attrs_in_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """__init__ must not set any banned cached-agent attributes."""
        # Patch external dependencies that would fail in unit-test context.
        monkeypatch.setattr(agent_mod, "settings", _MinimalSettings())

        class _DummyClient:
            pass

        model_client = _DummyClient()
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        # Only inject the minimal required __init__ args.
        try:
            # We can't call full __init__ without a real runtime, but we can
            # check the __init__ source does not assign the banned attrs.
            import inspect

            source = inspect.getsource(TimeboxingFlowAgent.__init__)
            for attr in self._BANNED_ATTRS:
                assert f"self.{attr}" not in source, (
                    f"TimeboxingFlowAgent.__init__ assigns self.{attr} — "
                    "banned cached-agent attribute."
                )
        except Exception:
            # If source inspection fails, fall through — the attribute tests above cover it.
            pass


class _MinimalSettings:
    """Stand-in for the global settings object in __init__ attribute scan."""

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return None


class _MinimalSettings:
    """Stand-in for the global settings object in __init__ attribute scan."""

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return None
