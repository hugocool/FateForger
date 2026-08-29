"""The Slack timeboxing route reads its state from the session, not the thread.

Every assertion here exists because the 2026-08-29 incident was caused by
reconstructing a planning session from `conversations_replies`. A transcript
records what was said; it does not record what was decided, so the day drifted
from Saturday to Friday and an approved skeleton was asked for twice. These
tests drive the real Slack seams against a migrated database and a planner
double, and one of them refuses the transcript API outright.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from alembic.config import Config
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    PlannerPort,
    ProgressSink,
)
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    DayType,
    FactKind,
    PlanningBrief,
    PlanningFact,
    PlanningResult,
    ProvidePlanningFacts,
)
from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    TimeboxCommitMeta,
)
from fateforger.slack_bot.timeboxing_session_store import (
    SqlAlchemyTimeboxingSessionRepository,
)

TZ = "Europe/Amsterdam"
SATURDAY = date(2026, 8, 29)


class ExplodingPlanner(PlannerPort):
    """A planner that must never run. Stage 0 has nothing to plan yet."""

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        raise AssertionError("the date card must not launch a planner")


class RecordedPlanner(PlannerPort):
    """Stand in for DeepSeek and keep every brief it was handed."""

    def __init__(self, results: list[PlanningResult] | None = None) -> None:
        self.briefs: list[PlanningBrief] = []
        self._results = list(results or [])

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        self.briefs.append(brief)
        if self._results:
            return self._results.pop(0)
        return _skeleton_result()


def _skeleton_result() -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.SKELETON,
                payload={"markdown": "## Saturday\n- 17:00 Gym"},
                dependency_revisions={"planning_day": 1},
            )
        ]
    )


class Runtime:
    """Only the attributes the kernel route is allowed to read."""

    def __init__(self, *, repository: Any, planner: Any) -> None:
        self.timeboxing_session_store = repository
        self.timeboxing_planner = planner

    async def send_message(self, message: Any, recipient: Any) -> Any:
        raise AssertionError("the kernel route must not reach the AutoGen runtime")


class Client:
    """A Slack double whose transcript API is a tripwire."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.updates: list[dict] = []
        self._ts = 0

    async def chat_postMessage(self, **payload: Any) -> dict:
        self.posted.append(payload)
        self._ts += 1
        return {"channel": payload["channel"], "ts": f"p{self._ts}"}

    async def chat_update(self, **payload: Any) -> dict:
        self.updates.append(payload)
        return {"ok": True}

    async def chat_getPermalink(self, **_payload: Any) -> dict:
        return {"permalink": "https://slack.example/thread"}

    async def conversations_open(self, **_payload: Any) -> dict:
        return {"channel": {"id": "D1"}}

    async def conversations_replies(self, **_payload: Any) -> dict:
        raise AssertionError(
            "session state must come from the repository, not the transcript"
        )


@pytest.fixture(autouse=True)
def _harness_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")


@pytest.fixture(autouse=True)
def _no_background_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thread memory is another feature's write path and not under test here."""

    import fateforger.slack_bot.thread_memory as thread_memory

    async def _remember(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(thread_memory, "remember", _remember)


@pytest.fixture
def sessionmaker_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> async_sessionmaker:
    database_path = tmp_path / "adaptive-sessions.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def repository(
    sessionmaker_factory: async_sessionmaker,
) -> SqlAlchemyTimeboxingSessionRepository:
    return SqlAlchemyTimeboxingSessionRepository(sessionmaker_factory)


@pytest.fixture(autouse=True)
def _host_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the host clock so the proposed day is Saturday 2026-08-29."""

    monkeypatch.setattr(
        handlers,
        "_timeboxing_host_now",
        lambda: datetime(2026, 8, 29, 9, 0, tzinfo=ZoneInfo(TZ)),
    )


def _focus() -> FocusManager:
    return FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])


def _blocks_with_actions(payloads: list[dict]) -> list[dict]:
    return [payload for payload in payloads if payload.get("blocks")]


def _action_ids(blocks: list[dict]) -> set[str]:
    return {
        element.get("action_id")
        for block in blocks
        for element in block.get("elements") or ()
        if isinstance(element, dict)
    }


def _confirm_meta(blocks: list[dict]) -> TimeboxCommitMeta:
    for block in blocks:
        for element in block.get("elements") or ():
            if (
                isinstance(element, dict)
                and element.get("action_id") == FF_TIMEBOX_COMMIT_START_ACTION_ID
            ):
                meta = TimeboxCommitMeta.from_value(element.get("value") or "")
                assert meta is not None
                return meta
    raise AssertionError("no date-card confirm control was rendered")


async def test_timebox_start_renders_the_date_card_and_starts_no_planner(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """Stage 0 is a question the host already knows how to ask.

    Launching a planner here is what let a fresh process choose its own day.
    """

    planner = ExplodingPlanner()
    client = Client()

    await handlers.route_slack_event(
        runtime=Runtime(repository=repository, planner=planner),
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={"channel": "C1", "user": "U1", "text": "plan my day", "ts": "111"},
        bot_user_id=None,
        say=None,
        client=client,
    )

    carded = _blocks_with_actions(client.updates)
    assert carded, client.updates
    blocks = carded[-1]["blocks"]
    assert {
        FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
        FF_TIMEBOX_COMMIT_START_ACTION_ID,
    } <= _action_ids(blocks)

    meta = _confirm_meta(blocks)
    assert meta.session_key == "C1:p1"
    assert meta.date == SATURDAY.isoformat()
    snapshot = await repository.load_or_create("C1:p1", owner_user_id="U1")
    assert meta.expected_revision == snapshot.revision


class ScriptedInterpreter:
    """Stand in for the schema-bound interpreter; the model is not under test."""

    def __init__(self, intents: list[Any]) -> None:
        self._intents = list(intents)
        self.seen: list[str] = []

    async def interpret(self, user_text: str, snapshot: Any) -> Any:
        self.seen.append(user_text)
        if not self._intents:
            raise AssertionError("the route asked for one interpretation too many")
        return self._intents.pop(0)


def _fact(fact_id: str, kind: FactKind, value: Any) -> PlanningFact:
    return PlanningFact(fact_id=fact_id, kind=kind, value=value, source="user")


async def _start_and_confirm_saturday(
    *,
    runtime: Runtime,
    client: Client,
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> str:
    """Render the date card, press Confirm, and return the session key."""

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={"channel": "C1", "user": "U1", "text": "plan my day", "ts": "111"},
        bot_user_id=None,
        say=None,
        client=client,
    )
    meta = _confirm_meta(_blocks_with_actions(client.updates)[-1]["blocks"])
    await handlers._handle_timebox_date_confirmation(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=meta.to_value(),
        prompt_channel_id="C1",
        prompt_ts="p1",
        actor_user_id="U1",
        interaction_id="confirm-1",
    )
    return meta.session_key


async def test_confirming_the_card_locks_saturday_as_a_weekend_the_host_derived(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """2026-08-29 is a Saturday because the calendar says so, not the plan.

    The incident inverted this: the model read a day full of work and decided
    it must be a working Friday. Weekday and day type are arithmetic here.
    """

    planner = RecordedPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [ProvidePlanningFacts(facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")])]
    )
    client = Client()

    session_key = await _start_and_confirm_saturday(
        runtime=runtime, client=client, repository=repository
    )

    snapshot = await repository.load_or_create(session_key, owner_user_id="U1")
    assert snapshot.planning_day is not None
    assert snapshot.planning_day.date == SATURDAY
    assert snapshot.planning_day.iso_weekday == 6
    assert snapshot.planning_day.day_type is DayType.WEEKEND
    assert snapshot.planning_day.classification_basis == "calendar"

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "supermarket, gym, dinner at 19:30",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    assert planner.briefs, "the first planner brief was never built"
    assert planner.briefs[0].locked_day == snapshot.planning_day
    assert planner.briefs[0].target_artifact is ArtifactKind.SKELETON


async def test_a_fresh_repository_rehydrates_the_session_without_the_transcript(
    sessionmaker_factory: async_sessionmaker,
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """A bot restart must not cost the session its day or its facts.

    `Client.conversations_replies` raises, so any attempt to rebuild state from
    the thread fails this test rather than quietly half-working.
    """

    planner = RecordedPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [
            ProvidePlanningFacts(
                facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")]
            ),
            ProvidePlanningFacts(
                facts=[_fact("a2", FactKind.REQUESTED_ACTIVITY, "supermarket")]
            ),
        ]
    )
    client = Client()

    session_key = await _start_and_confirm_saturday(
        runtime=runtime, client=client, repository=repository
    )
    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "gym today",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    # The process that knew any of this is gone.
    runtime.timeboxing_session_store = SqlAlchemyTimeboxingSessionRepository(
        sessionmaker_factory
    )

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "supermarket too",
            "ts": "333",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    assert len(planner.briefs) == 2
    restarted = planner.briefs[1]
    assert restarted.locked_day.date == SATURDAY
    assert restarted.locked_day.day_type is DayType.WEEKEND
    assert {fact.fact_id for fact in restarted.facts} == {"a1", "a2"}
    assert restarted.session_key == session_key


def _artifact_action_value(blocks: list[dict], action_id: str) -> str:
    for block in blocks:
        for element in block.get("elements") or ():
            if isinstance(element, dict) and element.get("action_id") == action_id:
                return str(element.get("value") or "")
    raise AssertionError(f"no {action_id} control was rendered")


def _candidate_result() -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.VALIDATED_CANDIDATE,
                payload={
                    "digest": "d" * 64,
                    "rendered": "17:00 Gym",
                    "snapshot": {"calendar_id": "cal", "day": SATURDAY.isoformat()},
                    "patch": {"ops": []},
                },
                dependency_revisions={"skeleton": 1},
            )
        ]
    )


class _ForbiddenTmbx:
    """Any remote call from Stage 3 is the boundary failure, not a detail."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def read(self, *_args: Any, **_kwargs: Any) -> dict:
        raise AssertionError("Stage 3 must not read the remote plan baseline")

    async def commit(self, *_args: Any, **_kwargs: Any) -> dict:
        raise AssertionError("Stage 3 must not commit anything")


async def test_a_skeleton_turn_touches_no_calendar_and_stores_no_candidate(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 3 presents. It does not read a baseline, patch, or store a draft.

    Every one of those is a step towards the calendar, and the approval that
    guards the calendar has not been given yet.
    """

    import fateforger.slack_bot.tmbx_client as tmbx_client
    import fateforger.slack_bot.validated_timebox_draft as draft_module

    monkeypatch.setattr(tmbx_client, "TmbxClient", _ForbiddenTmbx)

    def _forbidden_apply(*_args: Any, **_kwargs: Any) -> int:
        raise AssertionError("Stage 3 must not claim a plan_apply attempt")

    monkeypatch.setattr(
        draft_module, "claim_plan_apply_attempt", _forbidden_apply
    )

    planner = RecordedPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [ProvidePlanningFacts(facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")])]
    )
    client = Client()

    session_key = await _start_and_confirm_saturday(
        runtime=runtime, client=client, repository=repository
    )
    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "gym today",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    assert planner.briefs[-1].target_artifact is ArtifactKind.SKELETON
    assert planner.briefs[-1].allowed_outputs == {ArtifactKind.SKELETON}
    assert handlers.take_pending_approval(session_key) is None


async def test_only_an_approved_skeleton_unlocks_the_first_validated_candidate(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate gate is the approval, not the planner's willingness.

    Before the click the kernel keeps re-offering the skeleton; after it, the
    next turn is the first one whose target is a validated candidate.
    """

    import fateforger.slack_bot.tmbx_client as tmbx_client

    reads: list[tuple[str, str]] = []

    class _RecordingTmbx:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def read(self, calendar_id: str, day: str) -> dict:
            reads.append((calendar_id, day))
            return {"ok": True, "snapshot": {"calendar_id": calendar_id, "day": day}}

    class _ConstraintStore:
        async def query_constraints(
            self, *, filters: dict, limit: int
        ) -> list[dict]:
            self.filters = filters
            return []

    monkeypatch.setattr(tmbx_client, "TmbxClient", _RecordingTmbx)

    planner = RecordedPlanner([_skeleton_result(), _candidate_result()])
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [ProvidePlanningFacts(facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")])]
    )
    runtime.timeboxing_calendar_id = "cal"
    runtime.timeboxing_constraint_store = _ConstraintStore()
    client = Client()

    session_key = await _start_and_confirm_saturday(
        runtime=runtime, client=client, repository=repository
    )
    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "gym today",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    skeleton_blocks = _blocks_with_actions(client.updates)[-1]["blocks"]
    approve_value = _artifact_action_value(
        skeleton_blocks, handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID
    )
    assert len(planner.briefs) == 1

    await handlers._handle_timebox_artifact_action(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=approve_value,
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="approve-1",
    )

    assert len(planner.briefs) == 2
    assert planner.briefs[1].target_artifact is ArtifactKind.VALIDATED_CANDIDATE
    # The baseline is read once the candidate stage is entered, and for the
    # locked day rather than whatever day the model would have inferred.
    assert reads == [("cal", SATURDAY.isoformat())]
    assert handlers.take_pending_approval(session_key)


async def test_a_failed_turn_says_one_stable_thing_and_leaks_no_payload(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """A refused turn is not a place to paste whatever the provider said."""

    class _ExplodingPlanner(PlannerPort):
        async def produce(
            self, brief: PlanningBrief, progress: ProgressSink
        ) -> PlanningResult:
            raise RuntimeError("upstream-token-abcdef and a stack of internals")

    runtime = Runtime(repository=repository, planner=_ExplodingPlanner())
    runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [ProvidePlanningFacts(facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")])]
    )
    client = Client()

    await _start_and_confirm_saturday(
        runtime=runtime, client=client, repository=repository
    )
    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "gym today",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    rendered = " ".join(
        str(update.get("text") or "") + json.dumps(update.get("blocks") or [])
        for update in client.updates
    )
    assert handlers._TIMEBOX_TURN_FAILED_TEXT in rendered
    assert "upstream-token-abcdef" not in rendered


async def test_a_stale_card_click_changes_nothing_and_says_so_safely(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """A button drawn before the session moved on must not apply to what came
    after it. The revision in the metadata is what makes that decidable."""

    planner = RecordedPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    client = Client()

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={"channel": "C1", "user": "U1", "text": "plan my day", "ts": "111"},
        bot_user_id=None,
        say=None,
        client=client,
    )
    meta = _confirm_meta(_blocks_with_actions(client.updates)[-1]["blocks"])
    stale = meta.model_copy(update={"expected_revision": meta.expected_revision + 5})

    await handlers._handle_timebox_date_confirmation(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=stale.to_value(),
        prompt_channel_id="C1",
        prompt_ts="p1",
        actor_user_id="U1",
        interaction_id="confirm-stale",
    )

    snapshot = await repository.load_or_create(meta.session_key, owner_user_id="U1")
    assert snapshot.planning_day is None
    assert not planner.briefs
    assert any(
        handlers._TIMEBOX_TURN_FAILED_TEXT in str(update.get("text") or "")
        for update in client.updates
    )
