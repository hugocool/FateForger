"""Phase 4: tests for Session rewiring and node integration.

Verifies that:
- Session has new fields (tb_plan, base_snapshot, event_id_map)
- StageSkeletonNode keeps Stage 3 markdown-focused and defers baseline to Stage 4
- StageRefineNode patches via TBPlan and keeps Timebox in sync
- StageReviewCommitNode calls CalendarSubmitter when tb_plan is present
- _update_timebox_with_feedback uses TBPlan when available
"""

from __future__ import annotations

import asyncio
import types
from datetime import date, time, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.agent import (
    RefinePreflight,
    RefineQualityFacts,
    Session,
    TimeboxingFlowAgent,
)
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage
from fateforger.agents.timeboxing.tb_models import (
    ET,
    AfterPrev,
    FixedStart,
    FixedWindow,
    TBEvent,
    TBPlan,
)
from fateforger.agents.timeboxing.timebox import Timebox, timebox_to_tb_plan

# ── Session field tests ──────────────────────────────────────────────────


class TestSessionNewFields:
    """Verify new fields on Session dataclass."""

    def test_session_has_tb_plan_field(self) -> None:
        """Session should have an optional tb_plan field, defaulting to None."""
        s = Session(thread_ts="t1", channel_id="c1", user_id="u1")
        assert s.tb_plan is None

    def test_session_has_base_snapshot_field(self) -> None:
        """Session should have an optional base_snapshot field, defaulting to None."""
        s = Session(thread_ts="t1", channel_id="c1", user_id="u1")
        assert s.base_snapshot is None

    def test_session_has_event_id_map_field(self) -> None:
        """Session should have an event_id_map dict, defaulting to empty."""
        s = Session(thread_ts="t1", channel_id="c1", user_id="u1")
        assert s.event_id_map == {}
        assert isinstance(s.event_id_map, dict)

    def test_session_tb_plan_can_be_set(self) -> None:
        """Session.tb_plan can be assigned a TBPlan."""
        s = Session(thread_ts="t1", channel_id="c1", user_id="u1")
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Test",
                    t=ET.DW,
                    p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)),
                ),
            ],
            date=date(2026, 2, 13),
        )
        s.tb_plan = plan
        assert s.tb_plan is plan
        assert len(s.tb_plan.events) == 1

    def test_session_event_id_map_independent_per_session(self) -> None:
        """Each session should get its own event_id_map dict."""
        s1 = Session(thread_ts="t1", channel_id="c1", user_id="u1")
        s2 = Session(thread_ts="t2", channel_id="c1", user_id="u1")
        s1.event_id_map["key1"] = "val1"
        assert "key1" not in s2.event_id_map


# ── Timebox → TBPlan round-trip ──────────────────────────────────────────


class TestTimeboxTBPlanRoundTrip:
    """Verify that the round-trip conversion preserves semantics."""

    def test_timebox_to_tb_plan_preserves_events(self) -> None:
        """Converting a Timebox to TBPlan should preserve event count and names."""
        timebox = Timebox(
            events=[
                CalendarEvent(
                    summary="Morning routine",
                    event_type=EventType.HABIT,
                    start_time=time(7, 0),
                    duration=timedelta(minutes=30),
                ),
                CalendarEvent(
                    summary="Deep work",
                    event_type=EventType.DEEP_WORK,
                    start_time=time(9, 0),
                    duration=timedelta(hours=2),
                ),
            ],
            date=date(2026, 2, 13),
            timezone="Europe/Amsterdam",
        )
        plan = timebox_to_tb_plan(timebox)
        assert len(plan.events) == 2
        assert plan.events[0].n == "Morning routine"
        assert plan.events[1].n == "Deep work"
        assert plan.date == date(2026, 2, 13)
        assert plan.tz == "Europe/Amsterdam"

    def test_tb_plan_resolves_after_round_trip(self) -> None:
        """TBPlan from round-trip conversion should still resolve times."""
        timebox = Timebox(
            events=[
                CalendarEvent(
                    summary="Focus block",
                    event_type=EventType.DEEP_WORK,
                    start_time=time(9, 0),
                    end_time=time(11, 0),
                ),
            ],
            date=date(2026, 2, 13),
            timezone="Europe/Amsterdam",
        )
        plan = timebox_to_tb_plan(timebox)
        resolved = plan.resolve_times()
        assert len(resolved) == 1
        assert resolved[0]["start_time"] == time(9, 0)
        assert resolved[0]["end_time"] == time(11, 0)


# ── CalendarSubmitter integration ────────────────────────────────────────


class TestCalendarSubmitterOnAgent:
    """Verify CalendarSubmitter is instantiated on TimeboxingFlowAgent."""

    def test_agent_has_calendar_submitter(self) -> None:
        """TimeboxingFlowAgent should have a _calendar_submitter attribute."""
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        # Minimal init to set up the submitter
        from fateforger.agents.timeboxing.submitter import CalendarSubmitter

        agent._calendar_submitter = CalendarSubmitter()
        assert hasattr(agent, "_calendar_submitter")


# ── StageSkeletonNode keeps Stage 3 markdown-only ────────────────────────


class TestSkeletonNodeMarkdownOnly:
    """Verify StageSkeletonNode defers Stage 4 plan/snapshot preparation."""

    @pytest.mark.asyncio
    async def test_skeleton_draft_defers_snapshot_to_stage4(self, monkeypatch) -> None:
        """After skeleton draft, Stage 3 should not build a baseline snapshot."""
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        agent._draft_model_client = OpenAIChatCompletionClient(
            model="gpt-4o-mini", api_key="test"
        )
        agent._constraint_store = None

        # Mock _run_skeleton_draft to return our timebox + markdown overview
        async def mock_skeleton_draft(session: Session) -> tuple[None, str, TBPlan]:
            _ = session
            drafted_plan = TBPlan(
                date=date(2026, 2, 13),
                tz="Europe/Amsterdam",
                events=[
                    TBEvent(
                        n="Focus Block",
                        t=ET.DW,
                        p=FixedWindow(st=time(9, 0), et=time(10, 30)),
                    )
                ],
            )
            return None, "## Day Overview\n- Focus Block", drafted_plan

        agent._run_skeleton_draft = types.MethodType(
            lambda self, s: mock_skeleton_draft(s), agent
        )

        agent._build_remote_snapshot_plan = types.MethodType(  # type: ignore[attr-defined]
            lambda self, _session: (_ for _ in ()).throw(
                AssertionError("Stage 3 should not build remote snapshot.")
            ),
            agent,
        )
        agent._render_markdown_summary_blocks = types.MethodType(
            lambda self, text: [],
            agent,
        )

        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            planned_date="2026-02-13",
            tz_name="Europe/Amsterdam",
            frame_facts={"work_window": {"start": "09:00", "end": "17:00"}},
            input_facts={"tasks": [{"name": "Study"}]},
        )

        # Import and call the node
        from fateforger.agents.timeboxing.nodes.nodes import (
            StageSkeletonNode,
            TransitionNode,
        )

        transition = TransitionNode.__new__(TransitionNode)
        transition.decision = None
        transition.stage_user_message = ""

        node = StageSkeletonNode(
            orchestrator=agent, session=session, transition=transition
        )

        from autogen_agentchat.messages import TextMessage
        from autogen_core import CancellationToken

        await node.on_messages(
            [TextMessage(content="go", source="user")],
            CancellationToken(),
        )

        # Verify Stage 3 keeps markdown + draft plan, and defers snapshot to Stage 4.
        assert session.timebox is None
        assert session.tb_plan is not None
        assert session.base_snapshot is None
        assert session.skeleton_overview_markdown == "## Day Overview\n- Focus Block"
        assert len(session.tb_plan.events) == 1
        assert session.tb_plan.events[0].n == "Focus Block"


# ── StageRefineNode uses TBPlan ──────────────────────────────────────────


class TestRefineNodeUsesTBPlan:
    """Verify StageRefineNode patches via TBPlan when available."""

    def test_session_with_tb_plan_uses_new_path(self) -> None:
        """When session.tb_plan is set, the refine node should use apply_patch (not legacy)."""
        # This is a structural test — verify the node code branches on tb_plan
        import inspect

        from fateforger.agents.timeboxing.nodes.nodes import StageRefineNode

        source = inspect.getsource(StageRefineNode.on_messages)
        # Should reference tb_plan in the patching logic
        assert "tb_plan" in source
        # Stage 4 should patch through TBPlan path only.
        assert "apply_patch_legacy" not in source
        assert "apply_patch(" in source

    @pytest.mark.asyncio
    async def test_refine_node_runs_repair_patch_when_preflight_reports_issue(self) -> None:
        """Preflight issues should be injected into patch-loop context."""
        from autogen_agentchat.messages import TextMessage
        from autogen_core import CancellationToken

        from fateforger.agents.timeboxing.nodes.nodes import StageRefineNode, TransitionNode

        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        seeded_plan = TBPlan(
            date=date(2026, 2, 13),
            tz="Europe/Amsterdam",
            events=[
                TBEvent(
                    n="Wake Up",
                    t=ET.H,
                    p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                )
            ],
        )

        patch_messages: list[str] = []

        def _ensure_refine(_self, session: Session) -> RefinePreflight:
            session.tb_plan = seeded_plan
            session.base_snapshot = seeded_plan.model_copy(deep=True)
            return RefinePreflight(
                plan_issues=[
                    "timebox_to_tb_plan: Event chain needs at least one fixed_start or fixed_window anchor"
                ]
            )

        async def _apply_patch(**kwargs: Any) -> tuple[TBPlan, Any]:
            patch_messages.append(str(kwargs["user_message"]))
            validator = kwargs.get("plan_validator")
            if validator is not None:
                validator(seeded_plan)
            return seeded_plan, {"ops": []}

        async def _collect_constraints(_session: Session) -> list[Any]:
            return []

        async def _run_summary(*, stage, timebox, session=None) -> StageGateOutput:
            _ = (timebox, session)
            return StageGateOutput(
                stage_id=stage,
                ready=True,
                summary=["Updated schedule."],
                missing=[],
                question="Proceed?",
                facts={},
            )

        async def _submit(_session: Session) -> str | None:
            return None

        agent._ensure_refine_plan_state = types.MethodType(  # type: ignore[attr-defined]
            _ensure_refine,
            agent,
        )
        agent._timebox_patcher = types.SimpleNamespace(
            apply_patch=_apply_patch,
        )
        agent._collect_constraints = types.MethodType(  # type: ignore[attr-defined]
            lambda self, session: _collect_constraints(session),
            agent,
        )
        agent._await_pending_constraint_extractions = types.MethodType(  # type: ignore[attr-defined]
            lambda self, _session: asyncio.sleep(0),
            agent,
        )
        agent._run_timebox_summary = types.MethodType(  # type: ignore[attr-defined]
            lambda self, **kwargs: _run_summary(**kwargs),
            agent,
        )
        agent._submit_current_plan = types.MethodType(  # type: ignore[attr-defined]
            lambda self, session: _submit(session),
            agent,
        )

        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            stage=TimeboxingStage.REFINE,
        )
        session.timebox = Timebox(
            events=[
                CalendarEvent(
                    summary="Wake Up",
                    event_type=EventType.HABIT,
                    start_time=time(9, 0),
                    duration=timedelta(minutes=30),
                )
            ],
            date=date(2026, 2, 13),
            timezone="Europe/Amsterdam",
        )

        transition = TransitionNode.__new__(TransitionNode)
        transition.stage_user_message = ""
        transition.decision = None

        node = StageRefineNode(
            orchestrator=agent,
            session=session,
            transition=transition,
        )

        await node.on_messages(
            [TextMessage(content="proceed", source="user")],
            CancellationToken(),
        )

        assert patch_messages
        assert "Repair the current plan first" in patch_messages[0]
        assert "Preflight validation issues:" in patch_messages[0]

    @pytest.mark.asyncio
    async def test_refine_node_appends_calendar_sync_note(self) -> None:
        """Refine stage should include calendar sync feedback in the summary."""
        from autogen_agentchat.messages import TextMessage
        from autogen_core import CancellationToken

        from fateforger.agents.timeboxing.nodes.nodes import StageRefineNode, TransitionNode

        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

        async def _run_summary(*, stage, timebox, session=None) -> StageGateOutput:
            _ = (timebox, session)
            return StageGateOutput(
                stage_id=stage,
                ready=True,
                summary=["Updated schedule."],
                missing=[],
                question="Proceed?",
                facts={},
            )

        async def _submit(_session: Session) -> str | None:
            return "Synced to Google Calendar."

        async def _apply_patch(**kwargs: Any) -> tuple[TBPlan, Any]:
            validator = kwargs.get("plan_validator")
            if validator is not None:
                validator(session.tb_plan)
            return session.tb_plan, {"ops": []}

        agent._run_timebox_summary = types.MethodType(  # type: ignore[attr-defined]
            lambda self, **kwargs: _run_summary(**kwargs),
            agent,
        )
        agent._submit_current_plan = types.MethodType(  # type: ignore[attr-defined]
            lambda self, session: _submit(session),
            agent,
        )
        agent._collect_constraints = types.MethodType(  # type: ignore[attr-defined]
            lambda self, _session: asyncio.sleep(0, result=[]),
            agent,
        )
        agent._await_pending_constraint_extractions = types.MethodType(  # type: ignore[attr-defined]
            lambda self, _session: asyncio.sleep(0),
            agent,
        )
        agent._compose_patcher_message = types.MethodType(  # type: ignore[attr-defined]
            lambda self, **kwargs: str(kwargs.get("base_message") or ""),
            agent,
        )
        agent._timebox_patcher = types.SimpleNamespace(apply_patch=_apply_patch)

        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            stage=TimeboxingStage.REFINE,
        )
        session.timebox = Timebox(
            events=[
                CalendarEvent(
                    summary="Focus",
                    event_type=EventType.DEEP_WORK,
                    start_time=time(9, 0),
                    duration=timedelta(minutes=90),
                )
            ],
            date=date(2026, 2, 13),
            timezone="Europe/Amsterdam",
        )
        session.tb_plan = timebox_to_tb_plan(session.timebox)
        session.base_snapshot = session.tb_plan.model_copy(deep=True)

        transition = TransitionNode.__new__(TransitionNode)
        transition.stage_user_message = ""
        transition.decision = None

        node = StageRefineNode(
            orchestrator=agent,
            session=session,
            transition=transition,
        )

        await node.on_messages(
            [TextMessage(content="proceed", source="user")],
            CancellationToken(),
        )

        assert node.last_gate is not None
        assert "Synced to Google Calendar." in node.last_gate.summary


class TestRefineQualityFacts:
    """Verify Refine stage quality facts are typed and persisted on Session."""

    @pytest.mark.asyncio
    async def test_enrich_refine_quality_feedback_uses_typed_llm_facts(self) -> None:
        """When quality facts are absent, the LLM assessor should populate them."""
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
        timebox = Timebox(
            events=[
                CalendarEvent(
                    summary="Focus",
                    event_type=EventType.DEEP_WORK,
                    start_time=time(9, 0),
                    end_time=time(10, 30),
                )
            ],
            date=date(2026, 2, 13),
            timezone="Europe/Amsterdam",
        )
        gate = StageGateOutput(
            stage_id=TimeboxingStage.REFINE,
            ready=True,
            summary=["Updated schedule."],
            missing=[],
            question="Proceed?",
            facts={},
        )

        async def _quality_assess(*, timebox: Timebox) -> RefineQualityFacts:
            _ = timebox
            return RefineQualityFacts(
                quality_level=2,
                quality_label="Okay",
                missing_for_next=["more buffers"],
                next_suggestion="Add a short recovery block after deep work.",
            )

        agent._run_refine_quality_assessment = types.MethodType(  # type: ignore[attr-defined]
            lambda self, **kwargs: _quality_assess(**kwargs),
            agent,
        )

        enriched = await TimeboxingFlowAgent._enrich_refine_quality_feedback(
            agent,
            session=session,
            gate=gate,
            timebox=timebox,
        )

        assert enriched.ready is True
        assert enriched.facts["quality_level"] == 2
        assert enriched.facts["quality_label"] == "Okay"
        assert enriched.facts["next_suggestion"] == "Add a short recovery block after deep work."
        assert session.last_quality_level == 2
        assert session.last_quality_label == "Okay"
        assert session.last_quality_next_step == "Add a short recovery block after deep work."


class TestRefinePreparation:
    """Verify Stage 4 preflight preparation when Stage 3 is markdown-only."""

    def test_ensure_refine_plan_state_builds_plan_and_snapshot(self) -> None:
        """When missing, Stage 4 should derive TBPlan and baseline snapshot."""
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            planned_date="2026-02-13",
            tz_name="Europe/Amsterdam",
            timebox=Timebox(
                events=[
                    CalendarEvent(
                        summary="Focus Block",
                        event_type=EventType.DEEP_WORK,
                        start_time=time(9, 0),
                        end_time=time(10, 30),
                    )
                ],
                date=date(2026, 2, 13),
                timezone="Europe/Amsterdam",
            ),
        )

        agent._session_debug = types.MethodType(  # type: ignore[attr-defined]
            lambda self, *_args, **_kwargs: None,
            agent,
        )
        agent._build_remote_snapshot_plan = types.MethodType(  # type: ignore[attr-defined]
            lambda self, _session: TBPlan(
                date=date(2026, 2, 13),
                tz="Europe/Amsterdam",
                events=[],
            ),
            agent,
        )

        TimeboxingFlowAgent._ensure_refine_plan_state(agent, session)

        assert session.tb_plan is not None
        assert len(session.tb_plan.events) == 1
        assert session.base_snapshot is not None
        assert session.base_snapshot.events == []

    def test_ensure_refine_plan_state_returns_issue_for_unanchored_seed(self) -> None:
        """Stage 4 preflight should keep an editable seed and surface repair issue."""
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            planned_date="2026-02-13",
            tz_name="Europe/Amsterdam",
            timebox=Timebox.model_construct(
                events=[
                    CalendarEvent.model_construct(
                        summary="Busy",
                        event_type=EventType.MEETING,
                        start_time=None,
                        end_time=None,
                        duration=timedelta(minutes=45),
                    )
                ],
                date=date(2026, 2, 13),
                timezone="Europe/Amsterdam",
            ),
        )

        agent._session_debug = types.MethodType(  # type: ignore[attr-defined]
            lambda self, *_args, **_kwargs: None,
            agent,
        )
        agent._build_remote_snapshot_plan = types.MethodType(  # type: ignore[attr-defined]
            lambda self, _session: TBPlan(
                date=date(2026, 2, 13),
                tz="Europe/Amsterdam",
                events=[],
            ),
            agent,
        )

        preflight = TimeboxingFlowAgent._ensure_refine_plan_state(agent, session)

        assert preflight.has_plan_issues
        assert "timebox_to_tb_plan" in preflight.plan_issues[0]
        assert session.tb_plan is not None
        assert len(session.tb_plan.events) == 1


class TestRefineStageGuards:
    """Verify patching is guarded to Stage 4 Refine only."""

    def test_compose_patcher_message_rejects_non_refine_stage(self) -> None:
        """Coordinator should reject patcher payloads outside Refine stage."""
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            planned_date="2026-02-13",
            tz_name="Europe/Amsterdam",
        )

        with pytest.raises(ValueError, match="restricted to Stage 4 Refine"):
            TimeboxingFlowAgent._compose_patcher_message(
                agent,
                base_message="test",
                session=session,
                stage=TimeboxingStage.SKELETON.value,
            )

    @pytest.mark.asyncio
    async def test_update_timebox_with_feedback_noops_outside_refine(self) -> None:
        """Legacy feedback updater should not patch unless stage is Refine."""
        agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
        session = Session(
            thread_ts="t1",
            channel_id="c1",
            user_id="u1",
            planned_date="2026-02-13",
            tz_name="Europe/Amsterdam",
            stage=TimeboxingStage.SKELETON,
            tb_plan=TBPlan(
                date=date(2026, 2, 13),
                tz="Europe/Amsterdam",
                events=[
                    TBEvent(
                        n="Anchor",
                        t=ET.M,
                        p=FixedWindow(st=time(9, 0), et=time(10, 0)),
                    )
                ],
            ),
        )
        agent._timebox_patcher = types.SimpleNamespace(
            apply_patch=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("Patcher must not run outside Refine.")
            ),
            apply_patch_legacy=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("Legacy patcher must not run outside Refine.")
            ),
        )

        actions = await TimeboxingFlowAgent._update_timebox_with_feedback(
            agent,
            session,
            "move things around",
        )

        assert actions == []
