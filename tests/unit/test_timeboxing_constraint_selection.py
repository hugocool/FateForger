from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.constants import TIMEBOXING_LIMITS
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintDayOfWeek,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


def _constraint(
    *,
    name: str,
    uid: str,
    necessity: ConstraintNecessity = ConstraintNecessity.SHOULD,
    scope: ConstraintScope = ConstraintScope.PROFILE,
) -> Constraint:
    return Constraint(
        name=name,
        description=name,
        necessity=necessity,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=scope,
        hints={"uid": uid},
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
    )


def _aspect_hints(
    *,
    aspect_id: str,
    category: str,
    frame_slot: str | None = None,
    schedule_start: str | None = None,
    schedule_end: str | None = None,
    is_startup_prefetch: bool = False,
    is_conditional: bool = False,
    conditional_on_absent: list[str] | None = None,
    conditional_on_present: list[str] | None = None,
    excludes_aspect_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "aspect_classification": {
            "aspect_id": aspect_id,
            "aspect_label": aspect_id.replace("_", " ").title(),
            "category": category,
            "frame_slot": frame_slot,
            "is_startup_prefetch": is_startup_prefetch,
            "schedule_start": schedule_start,
            "schedule_end": schedule_end,
            "duration_min": None,
            "is_conditional": is_conditional,
            "conditional_on_absent": list(conditional_on_absent or []),
            "conditional_on_present": list(conditional_on_present or []),
            "excludes_aspect_ids": list(excludes_aspect_ids or []),
        }
    }


@pytest.mark.asyncio
async def test_collect_constraints_uses_relevant_durable_stage_sets() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.REFINE
    session.durable_constraints_by_stage = {
        TimeboxingStage.COLLECT_CONSTRAINTS.value: [_constraint(name="collect", uid="u-collect")],
        TimeboxingStage.SKELETON.value: [_constraint(name="skeleton", uid="u-skeleton")],
        TimeboxingStage.REFINE.value: [_constraint(name="refine", uid="u-refine")],
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert {item.name for item in active} == {"collect", "refine"}


@pytest.mark.asyncio
async def test_collect_constraints_logs_raw_and_applicable_active_counts() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    events: list[tuple[str, dict[str, object]]] = []
    agent._session_debug = lambda _session, event, **kwargs: events.append((event, kwargs))
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",  # Friday
    )
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    applicable = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Friday planning",
        description="Works on Friday",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        days_of_week=[ConstraintDayOfWeek.FR],
        hints={"uid": "applicable"},
    )
    wrong_day = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Monday only",
        description="Only for Monday",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        days_of_week=[ConstraintDayOfWeek.MO],
        hints={"uid": "wrong-day"},
    )
    expired = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Expired window",
        description="No longer applicable",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.DATESPAN,
        start_date="2026-02-01",
        end_date="2026-02-10",
        hints={"uid": "expired"},
    )
    session.durable_constraints_by_stage = {
        TimeboxingStage.COLLECT_CONSTRAINTS.value: [applicable, wrong_day, expired]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert [item.name for item in active] == ["Friday planning"]
    assert session.active_constraints_raw_count == 3
    assert session.active_constraints_applicable_count == 1
    snapshots = [payload for event, payload in events if event == "constraints_active_snapshot"]
    assert snapshots, "Expected constraints_active_snapshot debug event."
    assert snapshots[-1]["active_raw_count"] == 3
    assert snapshots[-1]["active_applicable_count"] == 1
    assert snapshots[-1]["active_filtered_out_count"] == 2


@pytest.mark.asyncio
async def test_collect_constraints_reconciles_and_filters_stage_relevance() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",
    )
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    session.durable_constraints_by_stage = {
        TimeboxingStage.COLLECT_CONSTRAINTS.value: [
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Legacy low signal",
                description="Old unstructured should preference.",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints={},
            ),
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Structured morning routine v1",
                description="Morning routine 1h",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints={
                    "aspect_classification": {
                        "aspect_id": "morning_routine",
                        "duration_min": 60,
                        "is_startup_prefetch": True,
                    }
                },
            ),
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Structured morning routine v2",
                description="Morning routine one hour",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints={
                    "aspect_classification": {
                        "aspect_id": "morning_routine",
                        "duration_min": 60,
                        "is_startup_prefetch": True,
                    }
                },
            ),
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Legacy deep work default",
                description="Old profile deep work preference.",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints={
                    "aspect_classification": {
                        "aspect_id": "deep_work",
                        "duration_min": 90,
                    }
                },
            ),
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Locked hard guardrail",
                description="Must keep hard stop.",
                necessity=ConstraintNecessity.MUST,
                status=ConstraintStatus.LOCKED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints={},
            ),
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Sci-fi reading breaks",
                description="Old hobby preference not requested now.",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints={
                    "aspect_classification": {
                        "aspect_id": "reading_break",
                        "duration_min": 15,
                    }
                },
            ),
            Constraint(
                user_id="u1",
                channel_id="c1",
                thread_ts="t1",
                name="Session ask deep work",
                description="Current-thread deep-work request",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.SESSION,
                hints={"aspect_classification": {"aspect_id": "deep_work"}},
            ),
            Constraint(
                user_id="u1",
                channel_id="c1",
                thread_ts="t1",
                name="Session ask",
                description="Current-thread specific request",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.SESSION,
                hints={},
            ),
        ]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert session.active_constraints_raw_count == 8
    assert session.active_constraints_applicable_count == 8
    assert session.active_constraints_selected_count == 4
    names = {item.name for item in active}
    assert "Legacy low signal" not in names
    assert "Structured morning routine v1" in names
    assert "Structured morning routine v2" not in names
    assert "Legacy deep work default" not in names
    assert "Sci-fi reading breaks" not in names
    assert "Locked hard guardrail" in names
    assert "Session ask deep work" in names
    assert "Session ask" in names


@pytest.mark.asyncio
async def test_collect_constraints_prefers_session_activity_over_profile_default_time() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",
    )
    session.stage = TimeboxingStage.REFINE
    profile_default = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Default gym in evening",
        description="Gym in the evening by default.",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        hints=_aspect_hints(
            aspect_id="gym_training",
            category="exercise",
            schedule_start="18:00",
            schedule_end="19:30",
        ),
    )
    session_override = Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name="Gym at noon",
        description="Gym should be at 12:00 today.",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.SESSION,
        hints=_aspect_hints(
            aspect_id="gym_training",
            category="exercise",
            schedule_start="12:00",
            schedule_end="13:30",
        ),
    )
    session.durable_constraints_by_stage = {
        TimeboxingStage.REFINE.value: [profile_default, session_override]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert [item.name for item in active] == ["Gym at noon"]


@pytest.mark.asyncio
async def test_collect_constraints_prefers_session_gym_and_planning_over_defaults() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",
    )
    session.stage = TimeboxingStage.REFINE
    session.durable_constraints_by_stage = {
        TimeboxingStage.REFINE.value: [
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Default gym in evening",
                description="Gym around 18:00",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints=_aspect_hints(
                    aspect_id="gym_training",
                    category="exercise",
                    schedule_start="18:00",
                    schedule_end="19:30",
                ),
            ),
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="Default planning slot",
                description="Planning around 14:00",
                necessity=ConstraintNecessity.SHOULD,
                status=ConstraintStatus.PROPOSED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
                hints=_aspect_hints(
                    aspect_id="planning_session",
                    category="work",
                    schedule_start="14:00",
                    schedule_end="15:00",
                ),
            ),
            Constraint(
                user_id="u1",
                channel_id="c1",
                thread_ts="t1",
                name="Gym at noon",
                description="Gym at 12:00 today.",
                necessity=ConstraintNecessity.MUST,
                status=ConstraintStatus.LOCKED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.SESSION,
                hints=_aspect_hints(
                    aspect_id="gym_training",
                    category="exercise",
                    schedule_start="12:00",
                    schedule_end="13:30",
                ),
            ),
            Constraint(
                user_id="u1",
                channel_id="c1",
                thread_ts="t1",
                name="Planning at 16:00",
                description="Planning session is at 16:00.",
                necessity=ConstraintNecessity.MUST,
                status=ConstraintStatus.LOCKED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.SESSION,
                hints=_aspect_hints(
                    aspect_id="planning_session",
                    category="work",
                    schedule_start="16:00",
                    schedule_end="17:00",
                ),
            ),
        ]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)
    names = {item.name for item in active}

    assert {"Gym at noon", "Planning at 16:00"}.issubset(names)
    assert "Default gym in evening" not in names
    assert "Default planning slot" not in names


@pytest.mark.asyncio
async def test_collect_constraints_suppresses_conditional_gym_when_other_sport_present() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    events: list[tuple[str, dict[str, object]]] = []
    agent._session_debug = lambda _session, event, **kwargs: events.append((event, kwargs))
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",
    )
    session.stage = TimeboxingStage.REFINE
    conditional_gym = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Gym default",
        description="Go to the gym unless another sport is already planned.",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        hints=_aspect_hints(
            aspect_id="gym_training",
            category="exercise",
            schedule_start="18:00",
            schedule_end="19:30",
            is_conditional=True,
            conditional_on_absent=["field_hockey"],
        ),
    )
    hockey_today = Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name="Field hockey tonight",
        description="Playing field hockey tonight.",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.SESSION,
        hints=_aspect_hints(
            aspect_id="field_hockey",
            category="exercise",
            schedule_start="19:00",
            schedule_end="21:00",
            excludes_aspect_ids=["gym_training"],
        ),
    )
    session.durable_constraints_by_stage = {
        TimeboxingStage.REFINE.value: [conditional_gym, hockey_today]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert [item.name for item in active] == ["Field hockey tonight"]
    snapshots = [payload for event, payload in events if event == "constraints_active_snapshot"]
    assert snapshots, "Expected constraints_active_snapshot debug event."
    assert snapshots[-1]["active_suppressed_count"] == 1
    assert snapshots[-1]["active_suppressed_reasons"] == ["conditional_on_absent"]


@pytest.mark.asyncio
async def test_collect_constraints_keeps_conditional_gym_without_blocker() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",
    )
    session.stage = TimeboxingStage.REFINE
    conditional_gym = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Gym default",
        description="Go to the gym unless another sport is already planned.",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        hints=_aspect_hints(
            aspect_id="gym_training",
            category="exercise",
            schedule_start="18:00",
            schedule_end="19:30",
            is_conditional=True,
            conditional_on_absent=["field_hockey"],
        ),
    )
    session.durable_constraints_by_stage = {
        TimeboxingStage.REFINE.value: [conditional_gym]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert [item.name for item in active] == ["Gym default"]


def test_select_constraints_for_refine_patcher_keeps_all_session_and_caps_durable() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.REFINE
    session_constraints = [
        _constraint(
            name=f"session-{idx}",
            uid=f"session-{idx}",
            scope=ConstraintScope.SESSION,
        )
        for idx in range(3)
    ]
    durable_constraints = [
        _constraint(
            name=f"must-{idx}",
            uid=f"must-{idx}",
            necessity=ConstraintNecessity.MUST,
        )
        for idx in range(2)
    ] + [
        _constraint(name=f"should-{idx}", uid=f"should-{idx}")
        for idx in range(TIMEBOXING_LIMITS.refine_patcher_constraint_limit + 20)
    ]
    constraints = [*session_constraints, *durable_constraints]

    selected = TimeboxingFlowAgent._select_constraints_for_refine_patcher(
        agent,
        session=session,
        constraints=constraints,
    )

    assert len(selected) == (
        len(session_constraints) + TIMEBOXING_LIMITS.refine_patcher_constraint_limit
    )
    assert {item.name for item in session_constraints}.issubset(
        {item.name for item in selected}
    )
    assert {"must-0", "must-1"}.issubset({item.name for item in selected})
