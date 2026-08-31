"""The Slack timeboxing route reads its state from the session, not the thread.

Every assertion here exists because the 2026-08-29 incident was caused by
reconstructing a planning session from `conversations_replies`. A transcript
records what was said; it does not record what was decided, so the day drifted
from Saturday to Friday and an approved skeleton was asked for twice. These
tests drive the real Slack seams against a migrated database and a planner
double, and one of them refuses the transcript API outright.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
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
    Advance,
    ArtifactDraft,
    ArtifactKind,
    BlockerOption,
    DayType,
    FactKind,
    PlanningBrief,
    PlanningFact,
    PlanningResult,
    ProvidePlanningFacts,
    UserBlockerDraft,
)
from fateforger.slack_bot import handlers
from fateforger.slack_bot.handlers import HarnessApproveActionPayload
from fateforger.slack_bot.timebox_candidate import PendingTimeboxCandidates
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.timeboxing_intents import (
    ArtifactActionMeta,
    TimeboxingIntentInterpreter,
)
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    FF_TIMEBOX_DAY_TYPE_ACTION_ID,
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
def _fresh_pending_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pending candidates live in a module global keyed by session.

    Two tests here share the session key `C1:p1`, so without this one test's
    unspent candidate is still sitting there for the next, and an assertion
    about what a turn stored would be reading the previous test's leftovers.
    """

    monkeypatch.setattr(
        handlers, "_pending_candidates", PendingTimeboxCandidates()
    )


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


class ScriptedModel:
    """Stand in for the model the real interpreter asks, and keep its prompts.

    The interpreter itself is under test here -- stubbing it out would leave the
    chat surface asserted only where a fake already agreed with it -- so what is
    faked is the one thing that cannot be: the judgement.
    """

    def __init__(self, *responses: dict[str, Any]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[Any] = []

    async def create(self, messages: Any, *, json_output: Any) -> Any:
        self.prompts.append(
            "\n".join(str(message.content) for message in messages)
        )
        self.schemas.append(json_output)
        if not self._responses:
            raise AssertionError("the route asked for one interpretation too many")
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


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


def _day_type_overrides(blocks: list[dict]) -> dict[DayType, str]:
    """Map each offered day-type override to the value its button carries."""

    offered: dict[DayType, str] = {}
    for block in blocks:
        for element in block.get("elements") or ():
            if not isinstance(element, dict):
                continue
            # One id per day type since 2026-08-30: Slack rejects a message
            # whose controls share one, so the family is matched by prefix over
            # ids this system minted, not by a single constant.
            if not str(element.get("action_id") or "").startswith(
                FF_TIMEBOX_DAY_TYPE_ACTION_ID
            ):
                continue
            meta = TimeboxCommitMeta.from_value(str(element.get("value") or ""))
            assert meta is not None
            assert meta.day_type is not None
            offered[meta.day_type] = str(element.get("value"))
    return offered


async def test_pressing_vacation_on_a_saturday_locks_vacation_not_the_weekend(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """Three of the five day types cannot be derived from a calendar at all.

    The host can see that 2026-08-29 is a Saturday. It cannot see that Hugo is
    on holiday, and the only way to say so today is prose a model has to
    interpret. A wrong answer here is the most expensive one available: a
    vacation day comes back carrying the whole working week, every rule wrong
    and every one of them plausible. So the five are buttons, and pressing one
    is a typed override rather than a sentence to be read.
    """

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

    blocks = _blocks_with_actions(client.updates)[-1]["blocks"]
    overrides = _day_type_overrides(blocks)
    assert set(overrides) == set(DayType)

    await handlers._handle_timebox_date_confirmation(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=overrides[DayType.VACATION],
        prompt_channel_id="C1",
        prompt_ts="p1",
        actor_user_id="U1",
        interaction_id="vacation-1",
    )

    snapshot = await repository.load_or_create("C1:p1", owner_user_id="U1")
    assert snapshot.planning_day is not None
    assert snapshot.planning_day.date == SATURDAY
    assert snapshot.planning_day.iso_weekday == 6
    assert snapshot.planning_day.day_type is DayType.VACATION
    # `PlanningDay` refuses a "calendar" basis that disagrees with the weekday,
    # so an override that forgot to say it was one would raise rather than lie.
    assert snapshot.planning_day.classification_basis == "user_override"


async def test_picking_another_day_keeps_the_day_type_row_on_the_card(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """Re-rendering the card must not quietly drop an affordance.

    The dropdown rebuilds the whole message, so anything the rebuild forgets
    disappears without an error. Losing the override row is exactly the failure
    it exists to prevent: the user picks Monday, then has no way left to say
    that Monday is a holiday except prose.
    """

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

    monday = date(2026, 8, 31)
    await handlers._handle_timebox_date_reselect(
        client=client,
        logger=handlers.logger,
        value=meta.to_value(),
        selected_date=monday.isoformat(),
        prompt_channel_id="C1",
        prompt_ts="p1",
    )

    rerendered = _blocks_with_actions(client.updates)[-1]["blocks"]
    overrides = _day_type_overrides(rerendered)
    assert set(overrides) == set(DayType)
    reconfirm = _confirm_meta(rerendered)
    assert reconfirm.date == monday.isoformat()
    assert reconfirm.session_key == meta.session_key
    assert reconfirm.expected_revision == meta.expected_revision
    # The row's own buttons move with the date, or a holiday press would lock
    # the day the user just navigated away from.
    holiday = TimeboxCommitMeta.from_value(overrides[DayType.HOLIDAY])
    assert holiday is not None
    assert holiday.date == monday.isoformat()


class FlakyPlanner(PlannerPort):
    """Fail the first invocation, then behave. Exactly the retry case."""

    def __init__(self, results: list[PlanningResult] | None = None) -> None:
        self.briefs: list[PlanningBrief] = []
        self._results = list(results or [])
        self._failures = 1

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        if self._failures:
            self._failures -= 1
            raise RuntimeError("the planner was unreachable")
        self.briefs.append(brief)
        if self._results:
            return self._results.pop(0)
        return _skeleton_result()


async def _drive_to_a_failed_turn(
    *,
    runtime: Runtime,
    client: Client,
    channel: str,
) -> str:
    """Lock the day, capture one activity, and let the planner fall over."""

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={"channel": channel, "user": "U1", "text": "plan my day", "ts": "111"},
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
        prompt_channel_id=channel,
        prompt_ts="p1",
        actor_user_id="U1",
        interaction_id=f"confirm-{channel}",
    )
    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": channel,
            "user": "U1",
            "text": "gym today",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )
    return meta.session_key


async def test_a_failed_turn_offers_retry_and_cancel_bound_to_the_session(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """`code` is minted by this system, so a retry needs no interpretation.

    The failure copy alone leaves the user retyping what they already said in
    order to get back to the same place. Retry is the one control that repeats
    the turn verbatim, and it is safe to offer because nothing about deciding
    to offer it depends on reading what anybody wrote.
    """

    planner = FlakyPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [ProvidePlanningFacts(facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")])]
    )
    client = Client()

    session_key = await _drive_to_a_failed_turn(
        runtime=runtime, client=client, channel="C1"
    )

    failed = _blocks_with_actions(client.updates)[-1]
    assert handlers._TIMEBOX_TURN_FAILED_TEXT in str(failed.get("text") or "")
    retry = ArtifactActionMeta.model_validate_json(
        _artifact_action_value(
            failed["blocks"], handlers.FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID
        )
    )
    cancel = ArtifactActionMeta.model_validate_json(
        _artifact_action_value(
            failed["blocks"], handlers.FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID
        )
    )
    snapshot = await repository.load_or_create(session_key, owner_user_id="U1")
    for meta in (retry, cancel):
        assert meta.session_key == session_key
        assert meta.expected_revision == snapshot.revision
    assert retry.decision == "advance"
    assert cancel.decision == "cancel"


async def test_retry_and_a_typed_advance_reach_one_executor_with_one_intent(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A button is an extra door, never a replacement for the typed one.

    Task 6 converged both surfaces on one typed intent and one executor
    precisely so that neither can drift. If pressing Retry took a different
    route from typing "go on then", the two would be two features wearing one
    name, and only one of them would keep working.
    """

    from fateforger.agents.timeboxing.adaptive_timeboxing import AdaptiveTimeboxing

    intents: list[tuple[str, Any]] = []
    original_turn = AdaptiveTimeboxing.turn

    async def spy_turn(self: Any, request: Any, *, progress: Any) -> Any:
        intents.append((request.session_key, request.intent))
        return await original_turn(self, request, progress=progress)

    monkeypatch.setattr(AdaptiveTimeboxing, "turn", spy_turn)

    executor_sessions: list[str] = []
    original_run = handlers._run_adaptive_timebox_turn

    async def spy_run(**kwargs: Any) -> Any:
        executor_sessions.append(kwargs["session_key"])
        return await original_run(**kwargs)

    monkeypatch.setattr(handlers, "_run_adaptive_timebox_turn", spy_run)

    pressed_planner = FlakyPlanner()
    pressed_runtime = Runtime(repository=repository, planner=pressed_planner)
    pressed_runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [ProvidePlanningFacts(facts=[_fact("a1", FactKind.REQUESTED_ACTIVITY, "gym")])]
    )
    pressed_client = Client()
    pressed_key = await _drive_to_a_failed_turn(
        runtime=pressed_runtime, client=pressed_client, channel="C1"
    )
    retry_value = _artifact_action_value(
        _blocks_with_actions(pressed_client.updates)[-1]["blocks"],
        handlers.FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
    )
    await handlers._handle_timebox_artifact_action(
        runtime=pressed_runtime,
        client=pressed_client,
        logger=handlers.logger,
        value=retry_value,
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="retry-1",
    )

    typed_planner = FlakyPlanner()
    typed_runtime = Runtime(repository=repository, planner=typed_planner)
    typed_runtime.timeboxing_intent_interpreter = ScriptedInterpreter(
        [
            ProvidePlanningFacts(
                facts=[_fact("b1", FactKind.REQUESTED_ACTIVITY, "gym")]
            ),
            Advance(),
        ]
    )
    typed_client = Client()
    typed_key = await _drive_to_a_failed_turn(
        runtime=typed_runtime, client=typed_client, channel="C2"
    )
    await handlers.route_slack_event(
        runtime=typed_runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C2",
            "user": "U1",
            "text": "you plan those things",
            "ts": "333",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=typed_client,
    )

    # One typed intent, arrived at from a press and from a sentence.
    assert [pair for pair in intents if isinstance(pair[1], Advance)] == [
        (pressed_key, Advance()),
        (typed_key, Advance()),
    ]
    # One executor, entered from both surfaces.
    assert {pressed_key, typed_key} <= set(executor_sessions)
    for planner in (pressed_planner, typed_planner):
        assert [brief.target_artifact for brief in planner.briefs] == [
            ArtifactKind.SKELETON
        ]


async def test_an_open_question_is_asked_without_a_button_row(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """The one thing the user is ever asked cannot be enumerated.

    `skeleton.requested_activity` is the catalog's only USER-owned `ask`, and
    what somebody wants out of their day has no closed answer set. A button row
    here would be a worse question, not a faster one, so its absence is
    asserted rather than left to taste.
    """

    planner = RecordedPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    client = Client()

    session_key = await _start_and_confirm_saturday(
        runtime=runtime, client=client, repository=repository
    )

    asked = [update for update in client.updates if update.get("blocks")][-1]
    snapshot = await repository.load_or_create(session_key, owner_user_id="U1")
    assert snapshot.planning_day is not None
    assert not planner.briefs
    text = str(asked.get("text") or "")
    assert "What do you want to get out of the day?" in text
    # The requirement id is how the catalog and the kernel address each other.
    # Rendering it was a sentence written for a debugger, shown to the person
    # being asked.
    assert "skeleton.requested_activity" not in text
    assert [
        block
        for block in asked.get("blocks") or ()
        if block.get("type") == "actions"
    ] == []


async def test_approving_a_superseded_skeleton_plans_nothing(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """A card outlives the thing it was drawn from, so it must name it.

    A second reply replaces the skeleton, and the button still sitting in the
    thread now points at a plan the session has already discarded. Approving it
    would carry the previous skeleton's approval onto the current one, which is
    how a candidate gets built from something nobody agreed to. The revision
    and digest in the metadata are what make that decidable without asking.
    """

    planner = RecordedPlanner([_skeleton_result(), _skeleton_result()])
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
    superseded = ArtifactActionMeta.model_validate_json(
        _artifact_action_value(
            _blocks_with_actions(client.updates)[-1]["blocks"],
            handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        )
    )
    assert superseded.artifact_id and superseded.artifact_digest
    assert superseded.artifact_revision == 1

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
    current = ArtifactActionMeta.model_validate_json(
        _artifact_action_value(
            _blocks_with_actions(client.updates)[-1]["blocks"],
            handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        )
    )
    assert current.artifact_id != superseded.artifact_id
    assert current.expected_revision != superseded.expected_revision

    planned_before = len(planner.briefs)
    await handlers._handle_timebox_artifact_action(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=superseded.model_dump_json(),
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="stale-approve-1",
    )

    snapshot = await repository.load_or_create(session_key, owner_user_id="U1")
    approved_artifacts = {approval.artifact_id for approval in snapshot.approvals}
    # Neither the plan the button was drawn on, nor -- and this is the failure
    # worth naming -- the plan that replaced it. A press retargeted at whatever
    # is current would look like a working approval and be one nobody gave.
    assert superseded.artifact_id not in approved_artifacts
    assert current.artifact_id not in approved_artifacts
    assert len(planner.briefs) == planned_before
    assert handlers.take_pending_approval(session_key) is None
    assert any(
        handlers._TIMEBOX_TURN_FAILED_TEXT in str(update.get("text") or "")
        for update in client.updates
    )


async def _drive_to_the_validated_candidate(
    *,
    runtime: Runtime,
    client: Client,
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> str:
    """Lock the day, capture an activity, approve the skeleton, get a candidate."""

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
    await handlers._handle_timebox_artifact_action(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=_artifact_action_value(
            _blocks_with_actions(client.updates)[-1]["blocks"],
            handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        ),
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="approve-skeleton",
    )
    return session_key


def _harness_approval_payload(blocks: list[dict]) -> HarnessApproveActionPayload:
    return HarnessApproveActionPayload.model_validate_json(
        _artifact_action_value(blocks, handlers.FF_HARNESS_APPROVE_ACTION_ID)
    )


async def test_approving_the_candidate_commits_it_and_records_the_receipt(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session has to learn that its own commit happened.

    Approval used to commit straight through tmbx without telling the kernel,
    so the calendar was written and the session still sat at
    `AwaitingApproval(validated_candidate)` -- the next turn would offer the
    same plan again, and nothing in the stored session recorded the write. The
    commit itself is unchanged: the same candidate, the same tmbx digest as its
    idempotency key. What is new is that the receipt comes back into the
    session.
    """

    import fateforger.slack_bot.tmbx_client as tmbx_client

    commits: list[tuple[dict, dict, str | None]] = []

    class _RecordingTmbx:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def read(self, calendar_id: str, day: str) -> dict:
            return {"ok": True, "snapshot": {"calendar_id": calendar_id, "day": day}}

        async def commit(
            self, snapshot: dict, patch: dict, *, idempotency_key: str | None = None
        ) -> dict:
            commits.append((snapshot, patch, idempotency_key))
            return {"committed": True, "tx_id": "tx-77"}

    class _ConstraintStore:
        async def query_constraints(self, *, filters: dict, limit: int) -> list[dict]:
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

    session_key = await _drive_to_the_validated_candidate(
        runtime=runtime, client=client, repository=repository
    )
    candidate_blocks = _blocks_with_actions(client.updates)[-1]["blocks"]
    approval = _harness_approval_payload(candidate_blocks)
    assert approval.thread_key == session_key
    # The button stamps the revision it was drawn at, so a press that arrives
    # after the session moved on is refused by the kernel, not by the renderer.
    assert approval.expected_revision is not None

    await handlers._handle_timebox_candidate_approval(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        approval=approval,
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="approve-candidate",
    )

    assert commits == [
        (
            {"calendar_id": "cal", "day": SATURDAY.isoformat()},
            {"ops": []},
            "d" * 64,
        )
    ]
    snapshot = await repository.load_or_create(session_key, owner_user_id="U1")
    assert snapshot.status == "committed"
    receipt = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.kind is ArtifactKind.COMMIT_RECEIPT
    ]
    assert len(receipt) == 1
    assert receipt[0].payload["tx_id"] == "tx-77"
    # The receipt names the artifact that was approved, not the tmbx payload
    # digest -- the latter is the idempotency key asserted above, and the two
    # answer different questions.
    candidate_artifact = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.kind is ArtifactKind.VALIDATED_CANDIDATE
    ][-1]
    assert receipt[0].payload["candidate_digest"] == candidate_artifact.digest
    assert receipt[0].dependency_revisions == {
        "validated_candidate": candidate_artifact.revision
    }
    # Spent exactly once: a second press has nothing left to commit.
    assert handlers.take_pending_approval(session_key) is None
    assert handlers.FF_HARNESS_UNDO_ACTION_ID in json.dumps(
        _blocks_with_actions(client.updates)[-1]["blocks"]
    )


def _stamped_identity(action_id: str, value: str) -> tuple[str, int]:
    """Decode any control this route renders into (session key, revision).

    Deliberately exhaustive, and deliberately raises on an id it does not
    recognise: the point of the sweep below is to fail when somebody adds a
    button that carries neither, and a decoder that shrugged at the unknown
    case would pass instead.
    """
    if action_id == FF_TIMEBOX_COMMIT_START_ACTION_ID or action_id.startswith(
        FF_TIMEBOX_DAY_TYPE_ACTION_ID
    ):
        meta = TimeboxCommitMeta.from_value(value)
        assert meta is not None
        return meta.session_key, meta.expected_revision
    if action_id in (
        handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        handlers.FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
        handlers.FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
        handlers.FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
    ):
        artifact_meta = ArtifactActionMeta.model_validate_json(value)
        return artifact_meta.session_key, artifact_meta.expected_revision
    if action_id == handlers.FF_HARNESS_APPROVE_ACTION_ID:
        approval = HarnessApproveActionPayload.model_validate_json(value)
        assert approval.expected_revision is not None
        return approval.thread_key, approval.expected_revision
    raise AssertionError(f"{action_id} renders a button that names no session")


def _stamps(blocks: list[dict]) -> set[tuple[str, int]]:
    return {
        _stamped_identity(
            str(element.get("action_id")), str(element.get("value") or "")
        )
        for block in blocks
        for element in block.get("elements") or ()
        if isinstance(element, dict) and element.get("type") == "button"
    }


async def test_every_rendered_control_names_its_session_and_revision(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A press arrives long after the message it sits in was drawn.

    Slack keeps a card alive forever, so the only thing that can tell a live
    press from one aimed at a session that has since moved is what the button
    itself carries. Stamping the session and the revision puts that decision in
    the kernel, where a mismatch is a refusal; leaving it out puts it in the
    renderer, which by then has no idea what it drew.
    """

    import fateforger.slack_bot.tmbx_client as tmbx_client

    class _RecordingTmbx:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def read(self, calendar_id: str, day: str) -> dict:
            return {"ok": True, "calendar_id": calendar_id, "day": day}

    class _ConstraintStore:
        async def query_constraints(self, *, filters: dict, limit: int) -> list[dict]:
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

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={"channel": "C1", "user": "U1", "text": "plan my day", "ts": "111"},
        bot_user_id=None,
        say=None,
        client=client,
    )
    session_key = "C1:p1"
    surfaces: list[set[tuple[str, int]]] = []
    revisions: list[int] = []

    async def _record() -> None:
        surfaces.append(_stamps(_blocks_with_actions(client.updates)[-1]["blocks"]))
        snapshot = await repository.load_or_create(session_key, owner_user_id="U1")
        revisions.append(snapshot.revision)

    await _record()

    await handlers._handle_timebox_date_confirmation(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=_confirm_meta(
            _blocks_with_actions(client.updates)[-1]["blocks"]
        ).to_value(),
        prompt_channel_id="C1",
        prompt_ts="p1",
        actor_user_id="U1",
        interaction_id="sweep-confirm",
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
    await _record()

    await handlers._handle_timebox_artifact_action(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=_artifact_action_value(
            _blocks_with_actions(client.updates)[-1]["blocks"],
            handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        ),
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="sweep-approve",
    )
    await _record()

    assert len(surfaces) == 3
    for surface, revision in zip(surfaces, revisions, strict=True):
        assert surface, "a reviewable surface rendered no control at all"
        assert surface == {(session_key, revision)}


async def test_a_typed_vacation_gets_past_the_date_card_without_a_press(
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> None:
    """The date card was the one stage chat could not answer at all.

    `_display_context` allowed exactly one decision before the day was locked --
    cancel -- so a session driven by typing stopped at stage 0 and every day
    type that a calendar cannot see was reachable only by pressing a button. An
    agent driving this session has no hands.
    """

    planner = RecordedPlanner()
    runtime = Runtime(repository=repository, planner=planner)
    model = ScriptedModel(
        {"decision": "confirm_planning_day", "day_type": "vacation", "facts": []}
    )
    runtime.timeboxing_intent_interpreter = TimeboxingIntentInterpreter(model)
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
    # The card is on screen and nobody pressed it.
    assert not model.prompts

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "go ahead, I'm on vacation this week",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )

    snapshot = await repository.load_or_create("C1:p1", owner_user_id="U1")
    assert snapshot.planning_day is not None
    assert snapshot.planning_day.date == SATURDAY
    assert snapshot.planning_day.iso_weekday == 6
    assert snapshot.planning_day.day_type is DayType.VACATION
    # The same basis the button records. `PlanningDay` refuses a `calendar`
    # basis that disagrees with the weekday, so a chat override that skipped it
    # would raise rather than quietly claim the calendar said so.
    assert snapshot.planning_day.classification_basis == "user_override"
    assert len(model.prompts) == 1


def _closed_choice_requirements() -> type:
    """Borrow the kernel tests' fake catalog rather than grow a second one.

    Nothing in the shipped catalog is a soft, user-owned gap yet (Task 15), so
    the option path has no live requirement to render and one has to be stood
    in. Defining a second fake here would be a second definition of the same
    intended requirement, and the two would drift the moment either moved.
    """

    module_path = Path(__file__).parents[1] / "unit" / "test_adaptive_timeboxing.py"
    spec = importlib.util.spec_from_file_location("_kernel_catalog", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._ClosedChoiceRequirements


def _shape_options() -> list[BlockerOption]:
    return [
        BlockerOption(
            option_id="option-1",
            label="Deep work first",
            effect="puts the gym after dinner, and the afternoon stays quiet",
        ),
        BlockerOption(
            option_id="option-2",
            label="Gym first",
            effect="puts deep work in the evening, after you have moved",
        ),
    ]


class ShapePlanner(PlannerPort):
    """Ask which shape the afternoon takes until told, then plan around it."""

    def __init__(self) -> None:
        self.briefs: list[PlanningBrief] = []

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        self.briefs.append(brief)
        if brief.target_artifact is ArtifactKind.VALIDATED_CANDIDATE:
            return _candidate_result()
        if any(fact.kind is FactKind.ORDINARY_PLACEMENT for fact in brief.facts):
            return _skeleton_result()
        return PlanningResult(
            blockers=[
                UserBlockerDraft(
                    requirement_id="skeleton.day_shape",
                    why_needed="three unallocated hours have two workable shapes",
                    options=_shape_options(),
                )
            ]
        )


def _option_presses(blocks: list[dict]) -> list[tuple[str, ArtifactActionMeta]]:
    """Every option control on a card, as (button text, decoded press)."""

    return [
        (
            str((element.get("text") or {}).get("text") or ""),
            ArtifactActionMeta.model_validate_json(str(element.get("value") or "")),
        )
        for block in blocks
        for element in block.get("elements") or ()
        if isinstance(element, dict)
        and element.get("action_id") == handlers.FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID
    ]


async def _ask_the_shape_question(
    *,
    runtime: Runtime,
    client: Client,
    repository: SqlAlchemyTimeboxingSessionRepository,
) -> str:
    """Lock the day by chat and let the planner put its closed question."""

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={"channel": "C1", "user": "U1", "text": "plan my day", "ts": "111"},
        bot_user_id=None,
        say=None,
        client=client,
    )
    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "go ahead",
            "ts": "222",
            "thread_ts": "p1",
        },
        bot_user_id=None,
        say=None,
        client=client,
    )
    return "C1:p1"


async def test_a_closed_question_arrives_as_buttons_carrying_what_they_answer(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed answer set rendered as a blank box is the worse question.

    The press arrives already typed and needs no interpretation at all, which is
    the whole reason Task 15 put options on the contract. Each button carries
    which question it answers and which answer it is, because the kernel checks
    both against the record it wrote when it asked.
    """

    monkeypatch.setattr(handlers, "TimeboxRequirements", _closed_choice_requirements())
    planner = ShapePlanner()
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = TimeboxingIntentInterpreter(
        ScriptedModel({"decision": "confirm_planning_day", "facts": []})
    )
    client = Client()

    session_key = await _ask_the_shape_question(
        runtime=runtime, client=client, repository=repository
    )

    asked = _blocks_with_actions(client.updates)[-1]
    presses = _option_presses(asked["blocks"])
    snapshot = await repository.load_or_create(session_key, owner_user_id="U1")

    assert [label for label, _ in presses] == ["Deep work first", "Gym first"]
    assert [meta.option_id for _, meta in presses] == ["option-1", "option-2"]
    for _, meta in presses:
        assert meta.decision == "choose_option"
        assert meta.requirement_id == "skeleton.day_shape"
        assert meta.session_key == session_key
        assert meta.expected_revision == snapshot.revision

    rendered = json.dumps(asked["blocks"])
    # The effect is a sentence and the button is a word or two. Truncating one
    # onto the other loses the half that says what the choice costs.
    for option in _shape_options():
        assert option.effect in rendered
        assert not any(option.effect in label for label, _ in presses)
    assert "Which shape should the afternoon take?" in str(asked.get("text") or "")


async def test_an_option_question_keeps_its_buttons_when_a_press_is_refused(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused press says so in a sentence, and leaves the question askable.

    `stale_blocker_choice` covers three causes on purpose -- no question open,
    the wrong question, an option nobody offered -- so that a crafted value
    learns nothing from which one it hit. That means one sentence has to be true
    of all three, and "that question has already been answered" is it.
    """

    monkeypatch.setattr(handlers, "TimeboxRequirements", _closed_choice_requirements())
    planner = ShapePlanner()
    runtime = Runtime(repository=repository, planner=planner)
    runtime.timeboxing_intent_interpreter = TimeboxingIntentInterpreter(
        ScriptedModel({"decision": "confirm_planning_day", "facts": []})
    )
    client = Client()

    await _ask_the_shape_question(
        runtime=runtime, client=client, repository=repository
    )
    _, offered = _option_presses(
        _blocks_with_actions(client.updates)[-1]["blocks"]
    )[0]
    forged = offered.model_copy(update={"option_id": "option-9"})

    await handlers._handle_timebox_artifact_action(
        runtime=runtime,
        client=client,
        logger=handlers.logger,
        value=forged.model_dump_json(),
        channel_id="C1",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="forged-press",
    )

    refused = _blocks_with_actions(client.updates)[-1]
    assert handlers._TIMEBOX_STALE_CHOICE_TEXT in str(refused.get("text") or "")
    assert handlers._TIMEBOX_TURN_FAILED_TEXT not in str(refused.get("text") or "")
    snapshot = await repository.load_or_create("C1:p1", owner_user_id="U1")
    # Nothing happened, so the question the user is looking at is still open.
    assert snapshot.pending_blocker is not None
    assert snapshot.pending_blocker.requirement_id == "skeleton.day_shape"


def _comparable(intent: Any) -> dict:
    """One typed intent, stripped of the identifiers a fresh session mints.

    `fact_id` and `artifact_id` are uuid4s drawn per session, so two runs of the
    same flow can never agree on them and comparing them would measure only
    that. Everything that decides anything -- the day and its basis, the
    question and the option, the revision and digest an approval is bound to --
    is kept.
    """

    dumped = intent.model_dump(mode="json")
    dumped.pop("artifact_id", None)
    for fact in dumped.get("facts") or ():
        fact.pop("fact_id", None)
    return dumped


class _RecordingTmbxRead:
    """Read the baseline the candidate stage needs; commit nothing."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def read(self, calendar_id: str, day: str) -> dict:
        return {"ok": True, "snapshot": {"calendar_id": calendar_id, "day": day}}


class _EmptyConstraintStore:
    async def query_constraints(self, *, filters: dict, limit: int) -> list[dict]:
        return []


def _shape_runtime(
    repository: SqlAlchemyTimeboxingSessionRepository,
    *,
    responses: list[dict[str, Any]],
) -> tuple[Runtime, ShapePlanner, ScriptedModel]:
    planner = ShapePlanner()
    runtime = Runtime(repository=repository, planner=planner)
    model = ScriptedModel(*responses)
    runtime.timeboxing_intent_interpreter = TimeboxingIntentInterpreter(model)
    runtime.timeboxing_calendar_id = "cal"
    runtime.timeboxing_constraint_store = _EmptyConstraintStore()
    return runtime, planner, model


async def _say(
    *, runtime: Runtime, client: Client, channel: str, text: str, ts: str
) -> None:
    """One typed reply in the session thread."""

    await handlers.route_slack_event(
        runtime=runtime,
        focus=_focus(),
        default_agent="timeboxing_agent",
        event={
            "channel": channel,
            "user": "U1",
            "text": text,
            "ts": ts,
            **({"thread_ts": "p1"} if ts != "111" else {}),
        },
        bot_user_id=None,
        say=None,
        client=client,
    )


async def test_a_whole_session_can_be_driven_by_chat_and_by_button_alike(
    repository: SqlAlchemyTimeboxingSessionRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every transition a button takes must also be reachable by typing.

    This is the requirement, not a convenience: an automated agent drives this
    session by text and has no hands. So the first half plays a complete
    session -- day confirmed as a vacation, facts, advance, the closed question
    answered, the skeleton approved, through to a validated candidate -- with
    nothing but interpreted replies. The second half plays the same session and
    presses every control that exists, and the two are compared as typed
    intents rather than as messages, because a second surface that produced a
    different intent would be a second feature wearing one name.

    `advance` is typed in both runs on purpose: the capture stage has no
    Proceed control yet, and pretending otherwise would hide that rather than
    record it.
    """

    monkeypatch.setattr(handlers, "TimeboxRequirements", _closed_choice_requirements())
    from fateforger.slack_bot import tmbx_client

    monkeypatch.setattr(tmbx_client, "TmbxClient", _RecordingTmbxRead)

    from fateforger.agents.timeboxing.adaptive_timeboxing import AdaptiveTimeboxing

    intents: list[tuple[str, Any]] = []
    original_turn = AdaptiveTimeboxing.turn

    async def spy_turn(self: Any, request: Any, *, progress: Any) -> Any:
        intents.append((request.session_key, request.intent))
        return await original_turn(self, request, progress=progress)

    monkeypatch.setattr(AdaptiveTimeboxing, "turn", spy_turn)

    executor_sessions: list[str] = []
    original_run = handlers._run_adaptive_timebox_turn

    async def spy_run(**kwargs: Any) -> Any:
        executor_sessions.append(kwargs["session_key"])
        return await original_run(**kwargs)

    monkeypatch.setattr(handlers, "_run_adaptive_timebox_turn", spy_run)

    facts_reply = {
        "decision": "provide_facts",
        "facts": [{"kind": "requested_activity", "value": "gym"}],
    }

    # -- typed, end to end ------------------------------------------------
    typed_runtime, typed_planner, typed_model = _shape_runtime(
        repository,
        responses=[
            {"decision": "confirm_planning_day", "day_type": "vacation", "facts": []},
            facts_reply,
            {"decision": "advance", "facts": []},
            {"decision": "choose_option", "option_id": "option-2"},
            {"decision": "approve", "facts": []},
        ],
    )
    typed_client = Client()

    await _say(
        runtime=typed_runtime,
        client=typed_client,
        channel="C1",
        text="plan my day",
        ts="111",
    )
    for step, (text, ts) in enumerate(
        [
            ("go ahead — I'm on vacation this week", "222"),
            ("gym at some point", "333"),
            ("you plan those things", "444"),
            ("gym first, I'll think in the evening", "555"),
            ("yes, that shape works", "666"),
        ]
    ):
        await _say(
            runtime=typed_runtime,
            client=typed_client,
            channel="C1",
            text=text,
            ts=ts,
        )
        assert len(typed_model.prompts) == step + 1

    typed_snapshot = await repository.load_or_create("C1:p1", owner_user_id="U1")
    assert typed_snapshot.planning_day is not None
    assert typed_snapshot.planning_day.day_type is DayType.VACATION
    assert typed_snapshot.planning_day.classification_basis == "user_override"
    assert [
        artifact.kind for artifact in typed_snapshot.artifacts
    ].count(ArtifactKind.VALIDATED_CANDIDATE) == 1
    assert handlers.take_pending_approval("C1:p1")

    # -- pressed, wherever a control exists -------------------------------
    pressed_runtime, pressed_planner, pressed_model = _shape_runtime(
        repository,
        responses=[facts_reply, {"decision": "advance", "facts": []}],
    )
    pressed_client = Client()

    await _say(
        runtime=pressed_runtime,
        client=pressed_client,
        channel="C2",
        text="plan my day",
        ts="111",
    )
    overrides = _day_type_overrides(
        _blocks_with_actions(pressed_client.updates)[-1]["blocks"]
    )
    await handlers._handle_timebox_date_confirmation(
        runtime=pressed_runtime,
        client=pressed_client,
        logger=handlers.logger,
        value=overrides[DayType.VACATION],
        prompt_channel_id="C2",
        prompt_ts="p1",
        actor_user_id="U1",
        interaction_id="press-vacation",
    )
    await _say(
        runtime=pressed_runtime,
        client=pressed_client,
        channel="C2",
        text="gym at some point",
        ts="333",
    )
    await _say(
        runtime=pressed_runtime,
        client=pressed_client,
        channel="C2",
        text="you plan those things",
        ts="444",
    )
    chosen = next(
        value
        for label, value in [
            (label, meta.model_dump_json())
            for label, meta in _option_presses(
                _blocks_with_actions(pressed_client.updates)[-1]["blocks"]
            )
        ]
        if label == "Gym first"
    )
    await handlers._handle_timebox_artifact_action(
        runtime=pressed_runtime,
        client=pressed_client,
        logger=handlers.logger,
        value=chosen,
        channel_id="C2",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="press-option",
    )
    await handlers._handle_timebox_artifact_action(
        runtime=pressed_runtime,
        client=pressed_client,
        logger=handlers.logger,
        value=_artifact_action_value(
            _blocks_with_actions(pressed_client.updates)[-1]["blocks"],
            handlers.FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        ),
        channel_id="C2",
        thread_ts="p1",
        actor_user_id="U1",
        interaction_id="press-approve",
    )

    pressed_snapshot = await repository.load_or_create("C2:p1", owner_user_id="U1")
    assert pressed_snapshot.planning_day == typed_snapshot.planning_day
    assert handlers.take_pending_approval("C2:p1")

    # -- one executor, one sequence of typed intents ----------------------
    typed_intents = [intent for key, intent in intents if key == "C1:p1"]
    pressed_intents = [intent for key, intent in intents if key == "C2:p1"]

    assert [intent.kind for intent in typed_intents] == [
        "start_session",
        "confirm_planning_day",
        "provide_planning_facts",
        "advance",
        "choose_blocker_option",
        "approve_artifact",
    ]
    assert [_comparable(intent) for intent in typed_intents] == [
        _comparable(intent) for intent in pressed_intents
    ]
    assert {"C1:p1", "C2:p1"} <= set(executor_sessions)
    # The typed choice was made from the offer rather than from a memory of it:
    # the turn that answered had the ids, the labels and the effects in front of
    # it, which is the whole difference between choosing and guessing.
    choice_prompt = typed_model.prompts[3]
    for option in _shape_options():
        assert option.option_id in choice_prompt
        assert option.label in choice_prompt
        assert option.effect in choice_prompt
    # Three of the pressed run's five transitions needed no model at all. That
    # is the case for buttons where the answer set is closed, and the case for
    # keeping both doors: the typed run needed five.
    assert len(pressed_model.prompts) == 2
    assert len(typed_model.prompts) == 5
    for planner in (typed_planner, pressed_planner):
        assert planner.briefs[-1].target_artifact is ArtifactKind.VALIDATED_CANDIDATE
