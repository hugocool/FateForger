import asyncio
import types
from datetime import datetime, timezone

import pytest

pytest.importorskip("autogen_agentchat")

import fateforger.agents.timeboxing.agent as timeboxing_agent_module
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.constraint_retriever import STARTUP_PREFETCH_TAG
from fateforger.agents.timeboxing.nlu import ConstraintInterpretation
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage
from fateforger.agents.timeboxing.preferences import (
    ConstraintBase,
    ConstraintDayOfWeek,
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)


@pytest.mark.asyncio
async def test_durable_constraint_prefetch_populates_session():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._durable_constraint_prefetch_tasks = {}
    agent._durable_constraint_prefetch_semaphore = asyncio.Semaphore(1)

    done = asyncio.Event()

    async def _fake_fetch(_self, _session, *, stage: TimeboxingStage):
        if stage == TimeboxingStage.SKELETON:
            done.set()
        return [
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="No early meetings",
                description="Avoid meetings before 09:00.",
                necessity=ConstraintNecessity.MUST,
                status=ConstraintStatus.LOCKED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
            )
        ]

    agent._fetch_durable_constraints = types.MethodType(_fake_fetch, agent)
    agent._collect_constraints = types.MethodType(
        lambda _self, _session: asyncio.sleep(0, result=[]), agent
    )
    agent._sync_durable_constraints_to_store = types.MethodType(
        lambda _self, _session, *, constraints: asyncio.sleep(0), agent
    )

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-01-21",
    )

    agent._queue_durable_constraint_prefetch(session=session, reason="test")

    await asyncio.wait_for(done.wait(), timeout=1.0)
    while session.pending_durable_constraints:
        await asyncio.sleep(0)

    assert TimeboxingStage.COLLECT_CONSTRAINTS.value in session.durable_constraints_by_stage
    assert TimeboxingStage.SKELETON.value in session.durable_constraints_by_stage
    assert TimeboxingStage.COLLECT_CONSTRAINTS.value in session.durable_constraints_loaded_stages
    assert TimeboxingStage.SKELETON.value in session.durable_constraints_loaded_stages
    assert session.pending_durable_constraints is False


@pytest.mark.asyncio
async def test_collect_constraints_merges_durable_with_session():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    durable = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Deep work block",
        description="Reserve 2 hours for deep work.",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
    )
    local = Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name="Gym",
        description="Gym at 18:00.",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.SESSION,
    )

    class _Store:
        async def list_constraints(self, **_kwargs):
            return [local]

    agent._constraint_store = _Store()

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
    )
    session.durable_constraints_by_stage[TimeboxingStage.COLLECT_CONSTRAINTS.value] = [durable]

    combined = await agent._collect_constraints(session)

    assert durable in combined
    assert local in combined
    assert durable in session.active_constraints
    assert local in session.active_constraints


@pytest.mark.asyncio
async def test_profile_constraints_auto_upsert_to_durable_store():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)
    agent._durable_constraint_semaphore = asyncio.Semaphore(1)
    agent._durable_constraint_task_keys = set()
    agent._constraint_extraction_tasks = {}
    agent._durable_constraint_prefetch_tasks = {}

    captured_adds: list[list[ConstraintBase]] = []
    captured_upserts: list[dict] = []
    prefetch_reasons: list[str] = []

    class _Store:
        async def add_constraints(self, **kwargs):
            captured_adds.append(list(kwargs["constraints"]))
            return []

    class _Client:
        async def upsert_constraint(self, *, record: dict, event: dict | None = None):
            captured_upserts.append({"record": record, "event": event})
            return {"uid": "tb_uid"}

    async def _fake_interpret(self, _session, *, text: str, is_initial: bool):
        _ = (text, is_initial)
        return ConstraintInterpretation(
            should_extract=True,
            scope="profile",
            constraints=[
                ConstraintBase(
                    name="No calls after 17:00",
                    description="Avoid meetings after 17:00.",
                    necessity=ConstraintNecessity.SHOULD,
                    scope=ConstraintScope.PROFILE,
                    status=ConstraintStatus.PROPOSED,
                    source=ConstraintSource.USER,
                    tags=["meetings"],
                )
            ],
        )

    async def _fake_collect(self, _session):
        return []

    async def _fake_ensure_store(self):
        return None

    agent._constraint_store = _Store()
    agent._ensure_constraint_store = types.MethodType(_fake_ensure_store, agent)
    agent._interpret_constraints = types.MethodType(_fake_interpret, agent)
    agent._collect_constraints = types.MethodType(_fake_collect, agent)
    agent._ensure_constraint_memory_client = types.MethodType(
        lambda _self: _Client(), agent
    )
    agent._queue_durable_constraint_prefetch = types.MethodType(
        lambda _self, *, session, reason: prefetch_reasons.append(reason), agent
    )

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", planned_date="2026-02-14")
    task = agent._queue_constraint_extraction(
        session=session,
        text="In general, no calls after 5pm.",
        reason="graphflow_turn",
        is_initial=False,
    )
    assert task is not None
    await task

    # Wait for the background durable upsert task to flush.
    for _ in range(50):
        if not agent._durable_constraint_task_keys:
            break
        await asyncio.sleep(0.01)

    assert captured_adds, "Expected local session constraints to be persisted."
    assert captured_upserts, "Expected durable upsert to be attempted."
    upsert_record = captured_upserts[0]["record"]["constraint_record"]
    assert upsert_record["scope"] == "profile"
    assert TimeboxingStage.COLLECT_CONSTRAINTS.value in upsert_record["applies_stages"]
    assert "DW" in upsert_record["applies_event_types"]
    assert "post_upsert" in prefetch_reasons


@pytest.mark.asyncio
async def test_await_pending_durable_prefetch_waits_for_task():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", planned_date="2026-02-14")
    task_key = agent._durable_prefetch_stage_key(
        session, stage=TimeboxingStage.COLLECT_CONSTRAINTS
    )
    slow_task = asyncio.create_task(asyncio.sleep(0.05))
    agent._durable_constraint_prefetch_tasks = {task_key: slow_task}
    agent._queue_durable_constraint_prefetch = types.MethodType(
        lambda _self, **_kwargs: None, agent
    )

    await agent._await_pending_durable_constraint_prefetch(
        session,
        timeout_s=0.5,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
    )
    assert slow_task.done() is True


def _durable_sleep_constraint(*, uid: str = "tb:sleep:default") -> Constraint:
    return Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Sleep schedule",
        description="Sleep at 23:00 and wake at 07:00.",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        tags=["sleep"],
        hints={"uid": uid, "rule_kind": "fixed_bedtime"},
    )


def test_collect_constraints_uses_durable_sleep_default_and_clears_sleep_missing() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.durable_constraints_by_stage[TimeboxingStage.COLLECT_CONSTRAINTS.value] = [
        _durable_sleep_constraint()
    ]

    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["Starting stage review."],
        missing=["Sleep schedule or morning/evening routines"],
        question="What are your sleep times?",
        facts={},
    )
    normalized = agent._normalize_collect_constraints_gate(
        session=session,
        gate=gate,
        user_message="",
    )

    assert normalized.ready is True
    assert normalized.missing == []
    assert normalized.facts.get("sleep_target", {}).get("start") == "23:00"
    assert normalized.facts.get("sleep_target", {}).get("end") == "07:00"
    assert normalized.question == (
        "Using your saved defaults. Reply to override for this session, or proceed."
    )
    assert any("Using your saved defaults:" in line for line in normalized.summary)


@pytest.mark.asyncio
async def test_collect_constraints_session_override_suppresses_durable_uid() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    durable = _durable_sleep_constraint(uid="tb:sleep:primary")
    session.durable_constraints_by_stage[TimeboxingStage.COLLECT_CONSTRAINTS.value] = [durable]
    agent._constraint_store = None

    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["Captured user updates."],
        missing=["sleep schedule"],
        question="Confirm sleep schedule?",
        facts={
            "sleep_target": {
                "start": "00:00",
                "end": "08:00",
                "hours": 8.0,
            }
        },
    )

    normalized = agent._normalize_collect_constraints_gate(
        session=session,
        gate=gate,
        user_message="For tomorrow, sleep 00:00 to 08:00.",
    )

    assert "tb:sleep:primary" in session.suppressed_durable_uids
    constraints = await agent._collect_constraints(session)
    assert durable not in constraints
    assert all(
        (c.hints or {}).get("uid") != "tb:sleep:primary"
        for c in session.active_constraints
    )

    context = agent._build_collect_constraints_context(session, user_message="")
    assert context["durable_constraints"] == []
    assert normalized.facts["sleep_target"]["start"] == "00:00"


def test_collect_constraints_does_not_claim_no_durable_constraints_while_prefetch_pending() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.pending_durable_constraints = True
    session.pending_durable_stages = {TimeboxingStage.COLLECT_CONSTRAINTS.value}
    session.durable_constraints_loaded_stages = set()

    gate = StageGateOutput(
        stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
        ready=False,
        summary=["No existing durable constraints found; we're starting with a clean canvas."],
        missing=["sleep target"],
        question="What time do you sleep?",
        facts={},
    )

    normalized = agent._normalize_collect_constraints_gate(
        session=session,
        gate=gate,
        user_message="",
    )

    summary_text = "\n".join(normalized.summary)
    assert "no existing durable constraints found" not in summary_text.lower()
    assert "still loading" in summary_text.lower()


def test_durable_upsert_record_marks_startup_prefetch_for_sleep_defaults() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", planned_date="2026-02-18")
    constraint = ConstraintBase(
        name="Sleep schedule",
        description="Sleep around 23:00 and wake around 07:00.",
        necessity=ConstraintNecessity.MUST,
        tags=["sleep"],
        scope=ConstraintScope.PROFILE,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
    )

    record = agent._build_durable_constraint_record(
        session=session,
        constraint=constraint,
        decision_scope="profile",
    )

    topics = record["constraint_record"]["topics"]
    assert "sleep" in topics
    assert STARTUP_PREFETCH_TAG in topics


def test_collect_constraints_context_includes_timezone_local_current_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 2, 27, 1, 34, tzinfo=timezone.utc)

    monkeypatch.setattr(timeboxing_agent_module, "datetime", _FixedDateTime)

    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-27",
        tz_name="Europe/Amsterdam",
    )
    context = agent._build_collect_constraints_context(session=session, user_message="")

    facts = context["facts"]
    assert facts["date"] == "2026-02-27"
    assert facts["timezone"] == "Europe/Amsterdam"
    assert facts["current_time"] == "02:34"
    assert facts["current_datetime"].startswith("2026-02-27T02:34")


def test_durable_uid_is_stable_when_description_wording_changes() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-27",
        tz_name="Europe/Amsterdam",
    )
    base = ConstraintBase(
        name="No calls after 17:00",
        description="Avoid meetings after 17:00.",
        necessity=ConstraintNecessity.SHOULD,
        scope=ConstraintScope.PROFILE,
        tags=["meetings"],
        hints={"rule_kind": "avoid_window"},
        selector={
            "windows": [
                {
                    "kind": "avoid",
                    "start_time_local": "17:00",
                    "end_time_local": "23:59",
                }
            ]
        },
    )
    revised = ConstraintBase(
        name="No calls after 17:00",
        description="Please keep late afternoon clear for focus.",
        necessity=ConstraintNecessity.MUST,
        scope=ConstraintScope.PROFILE,
        tags=["meetings"],
        hints={"rule_kind": "avoid_window"},
        selector={
            "windows": [
                {
                    "kind": "avoid",
                    "start_time_local": "17:00",
                    "end_time_local": "23:59",
                }
            ]
        },
    )

    uid_base = agent._build_durable_constraint_record(
        session=session,
        constraint=base,
        decision_scope="profile",
    )["constraint_record"]["lifecycle"]["uid"]
    uid_revised = agent._build_durable_constraint_record(
        session=session,
        constraint=revised,
        decision_scope="profile",
    )["constraint_record"]["lifecycle"]["uid"]

    assert uid_base == uid_revised


def test_durable_uid_normalizes_days_of_week_order() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-27",
        tz_name="Europe/Amsterdam",
    )
    c1 = ConstraintBase(
        name="Deep work weekdays",
        description="Protect deep work on weekdays.",
        necessity=ConstraintNecessity.SHOULD,
        scope=ConstraintScope.PROFILE,
        days_of_week=[ConstraintDayOfWeek.MO, ConstraintDayOfWeek.WE],
        hints={"rule_kind": "prefer_window"},
    )
    c2 = ConstraintBase(
        name="Deep work weekdays",
        description="Protect deep work on weekdays.",
        necessity=ConstraintNecessity.SHOULD,
        scope=ConstraintScope.PROFILE,
        days_of_week=[ConstraintDayOfWeek.WE, ConstraintDayOfWeek.MO],
        hints={"rule_kind": "prefer_window"},
    )

    uid1 = agent._build_durable_constraint_record(
        session=session,
        constraint=c1,
        decision_scope="profile",
    )["constraint_record"]["lifecycle"]["uid"]
    uid2 = agent._build_durable_constraint_record(
        session=session,
        constraint=c2,
        decision_scope="profile",
    )["constraint_record"]["lifecycle"]["uid"]

    assert uid1 == uid2
