from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    InMemoryPlanningSessionRepository,
    PlanningContext,
    ProgressSink,
    TurnRequest,
)
from fateforger.agents.timeboxing.durable_constraint_store import (
    ClientBackedDurableConstraintStore,
)
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactApproval,
    ArtifactKind,
    ArtifactSnapshot,
    DayType,
    FactKind,
    PlanningBrief,
    PlanningDay,
    PlanningFact,
    PlanningArtifact,
    PlanningResult,
    PlanningSessionSnapshot,
)
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.core import runtime as runtime_module
from fateforger.core.config import settings
from fateforger.slack_bot.deepseek_timebox_planner import (
    DeepSeekTimeboxPlanner,
    DependencyUnavailable,
    UnavailableConstraintReader,
)
from fateforger.slack_bot.tmbx_client import ReadUnavailable, TmbxClient
from memory.constraint_store import ConstraintStore
from memory.migrations import SCHEMA_VERSION


class _RecordedTool:
    def __init__(self, name: str, response: str) -> None:
        self.name = name
        self.response = response
        self.requests: list[dict[str, Any]] = []

    async def run_json(self, request: dict[str, Any], _token: object) -> str:
        self.requests.append(request)
        return self.response


class _RecordedMcpClient:
    def __init__(self, tools: list[_RecordedTool]) -> None:
        self.tools = tools

    async def get_tools(self) -> list[_RecordedTool]:
        return self.tools


class _SequencedMcpClient:
    def __init__(self, outcomes: list[list[_RecordedTool] | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def get_tools(self) -> list[_RecordedTool]:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _RecordedTmbx:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def read(self, calendar_id: str, day: str) -> dict[str, Any]:
        self.calls.append((calendar_id, day))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _RecordedConstraintReader:
    def __init__(self, rows: list[dict[str, Any]] | Exception) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def query_constraints(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


class _RecordedHarnessRunner:
    def __init__(self, result: PlanningResult | None = None) -> None:
        self.result = result or PlanningResult()
        self.calls: list[tuple[PlanningBrief, ProgressSink]] = []

    async def run(self, brief: PlanningBrief, progress: ProgressSink) -> PlanningResult:
        self.calls.append((brief, progress))
        return self.result


class _Progress:
    async def emit(self, event: object) -> None:
        _ = event


class _KernelContext:
    async def propose_planning_day(self, request: TurnRequest) -> PlanningDay:
        _ = request
        return _locked_day()

    async def resolve(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        target: ArtifactKind,
        progress: ProgressSink,
    ) -> PlanningContext:
        _ = (snapshot, target, progress)
        return PlanningContext()


class _ForbiddenCommit:
    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        raise AssertionError((candidate, digest))


def _client_with(*tools: _RecordedTool) -> TmbxClient:
    client = object.__new__(TmbxClient)
    client._client = _RecordedMcpClient(list(tools))
    return client


async def test_read_calls_exact_calendar_and_locked_day() -> None:
    """Catches a planner read drifting to a default calendar or host date."""

    tool = _RecordedTool(
        "plan_read",
        json.dumps(
            {
                "ok": True,
                "snapshot": {
                    "calendar_id": "hugo.evers@gmail.com",
                    "day": "2026-08-29",
                },
                "rendered": "",
                "blocks": [],
            }
        ),
    )
    client = _client_with(tool)

    payload = await client.read("hugo.evers@gmail.com", "2026-08-29")

    assert tool.requests == [
        {"calendar_id": "hugo.evers@gmail.com", "day": "2026-08-29"}
    ]
    assert payload["ok"] is True


async def test_read_retries_a_failed_tool_call_before_any_write() -> None:
    """Catches a retry-safe calendar read degrading after one transient error."""

    class _TransientTool(_RecordedTool):
        async def run_json(self, request: dict[str, Any], _token: object) -> str:
            self.requests.append(request)
            if len(self.requests) == 1:
                raise TimeoutError("private gateway response")
            return self.response

    tool = _TransientTool("plan_read", '{"ok":true,"snapshot":{}}')
    client = _client_with(tool)

    payload = await client.read("hugo.evers@gmail.com", "2026-08-29")

    assert payload["ok"] is True
    assert tool.requests == [
        {"calendar_id": "hugo.evers@gmail.com", "day": "2026-08-29"},
        {"calendar_id": "hugo.evers@gmail.com", "day": "2026-08-29"},
    ]


async def test_read_discovery_failure_is_sanitized_after_retry() -> None:
    """Catches provider payloads escaping through the typed read failure."""

    client = object.__new__(TmbxClient)
    sequenced = _SequencedMcpClient(
        [
            RuntimeError("private bearer token one"),
            RuntimeError("private bearer token two"),
        ]
    )
    client._client = sequenced

    with pytest.raises(ReadUnavailable) as caught:
        await client.read("hugo.evers@gmail.com", "2026-08-29")

    assert sequenced.calls == 2
    assert str(caught.value) == "calendar service unavailable"
    assert "private" not in str(caught.value)


async def test_read_retries_an_untrustworthy_response() -> None:
    """Catches a transient gateway body being accepted as calendar context."""

    class _SequencedTool(_RecordedTool):
        async def run_json(self, request: dict[str, Any], _token: object) -> str:
            self.requests.append(request)
            if len(self.requests) == 1:
                return "<html>private gateway body</html>"
            return self.response

    tool = _SequencedTool("plan_read", '{"ok":true,"snapshot":{}}')
    client = _client_with(tool)

    payload = await client.read("hugo.evers@gmail.com", "2026-08-29")

    assert payload == {"ok": True, "snapshot": {}}
    assert len(tool.requests) == 2


def _locked_day() -> PlanningDay:
    return PlanningDay(
        date=date(2026, 8, 29),
        timezone="Europe/Amsterdam",
        iso_weekday=6,
        day_type=DayType.WEEKEND,
        classification_basis="calendar",
        lock_revision=3,
    )


def _input_brief(*, facts: list[PlanningFact] | None = None) -> PlanningBrief:
    return PlanningBrief(
        session_key="C206:1777651200.0",
        base_revision=7,
        observed_at=datetime(2000, 1, 1, tzinfo=UTC),
        locked_day=_locked_day(),
        facts=facts
        or [
            PlanningFact(
                fact_id="fact-supermarket",
                kind=FactKind.REQUESTED_ACTIVITY,
                value={"activity": "supermarket"},
                source="user",
                source_interaction_id="1777651201.0",
            ),
            PlanningFact(
                fact_id="fact-gym",
                kind=FactKind.GYM,
                value={"requested": True},
                source="user",
                source_interaction_id="1777651202.0",
            ),
        ],
        assumptions=[],
        current_artifacts=[
            ArtifactSnapshot(
                artifact_id="day-frame-1",
                kind=ArtifactKind.DAY_FRAME,
                revision=2,
                digest="a" * 64,
                payload={"work_window": ["09:00", "17:30"]},
            ),
            ArtifactSnapshot(
                artifact_id="inputs-1",
                kind=ArtifactKind.CAPTURED_INPUTS,
                revision=4,
                digest="b" * 64,
                payload={"activities": ["supermarket", "gym"]},
            ),
        ],
        approvals=[],
        applicable_constraints={"stale": "must be replaced"},
        calendar_snapshot={"stale": "must be replaced"},
        target_artifact=ArtifactKind.SKELETON,
        readiness={"target_artifact": "skeleton"},
        allowed_outputs={ArtifactKind.SKELETON},
    )


def test_planner_requires_explicit_host_calendar_id() -> None:
    """Catches incident-specific calendar identity leaking into later sessions."""

    with pytest.raises(TypeError):
        DeepSeekTimeboxPlanner(
            tmbx_client=_RecordedTmbx({"ok": True, "snapshot": {}}),
            constraint_reader=_RecordedConstraintReader([]),
            clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
            harness_runner=_RecordedHarnessRunner(),
        )


async def test_produce_sends_one_complete_host_owned_brief_to_fresh_runner() -> None:
    """Catches omission, day drift, or stale external snapshots at the API seam."""

    calendar_payload = {
        "ok": True,
        "snapshot": {
            "calendar_id": "hugo.evers@gmail.com",
            "day": "2026-08-29",
            "events": [{"summary": "Dinner", "start": "18:00"}],
        },
        "rendered": "Dinner 18:00",
        "blocks": [],
    }
    constraints = [{"uid": "constraint-1", "name": "Bedtime", "status": "locked"}]
    observed_at = datetime(2026, 8, 29, 8, 12, 30, tzinfo=UTC)
    tmbx = _RecordedTmbx(calendar_payload)
    constraint_reader = _RecordedConstraintReader(constraints)
    runner = _RecordedHarnessRunner()
    progress = _Progress()
    source = _input_brief()
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=tmbx,
        constraint_reader=constraint_reader,
        calendar_id="hugo.evers@gmail.com",
        clock=lambda: observed_at,
        harness_runner=runner,
    )

    result = await planner.produce(source, progress)

    assert result == PlanningResult()
    assert tmbx.calls == [("hugo.evers@gmail.com", "2026-08-29")]
    assert constraint_reader.calls == [
        {
            "filters": {
                "planned_day": "2026-08-29",
                "day_type": "weekend",
                "require_active": True,
            },
            "limit": 200,
        }
    ]
    assert len(runner.calls) == 1
    complete, forwarded_progress = runner.calls[0]
    assert complete is not source
    assert complete.locked_day == _locked_day()
    assert complete.observed_at == observed_at
    assert complete.facts == source.facts
    assert complete.current_artifacts == source.current_artifacts
    assert complete.readiness == source.readiness
    assert complete.calendar_snapshot == calendar_payload
    assert complete.applicable_constraints == constraints
    assert complete.target_artifact is ArtifactKind.SKELETON
    assert complete.allowed_outputs == {ArtifactKind.SKELETON}
    assert forwarded_progress is progress


async def test_explicit_second_calendar_never_falls_back_to_incident_account() -> None:
    """Catches a non-Hugo session reading the incident calendar by default."""

    tmbx = _RecordedTmbx({"ok": True, "snapshot": {}})
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=tmbx,
        constraint_reader=_RecordedConstraintReader([]),
        calendar_id="second-calendar@example.com",
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
        harness_runner=_RecordedHarnessRunner(),
    )

    await planner.produce(_input_brief(), _Progress())

    assert tmbx.calls == [("second-calendar@example.com", "2026-08-29")]
    assert all(calendar_id != "hugo.evers@gmail.com" for calendar_id, _ in tmbx.calls)


async def test_actual_kernel_brief_preserves_exact_typed_approvals() -> None:
    """Catches the kernel dropping approval identity before the harness seam."""

    day_frame = PlanningArtifact.create(
        artifact_id="day-frame-approved",
        kind=ArtifactKind.DAY_FRAME,
        revision=2,
        payload={"work_window": ["09:00", "17:30"]},
        dependency_revisions={"planning_day": 1},
    )
    approval = ArtifactApproval(
        artifact_id=day_frame.artifact_id,
        artifact_revision=day_frame.revision,
        artifact_digest=day_frame.digest,
        actor_user_id="U206",
        session_revision=7,
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C206:kernel",
        revision=7,
        owner_user_id="U206",
        planning_day=_locked_day(),
        facts=[
            PlanningFact(
                fact_id="activity-1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="Write the proposal",
                source="user",
                source_interaction_id="1777651201.0",
            )
        ],
        artifacts=[day_frame],
        approvals=[approval],
    )
    runner = _RecordedHarnessRunner()
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=_RecordedTmbx({"ok": True, "snapshot": {}}),
        constraint_reader=_RecordedConstraintReader([]),
        calendar_id="work-calendar@example.com",
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
        harness_runner=runner,
    )
    kernel = AdaptiveTimeboxing(
        repository=InMemoryPlanningSessionRepository([snapshot]),
        requirements=TimeboxRequirements(),
        planner=planner,
        context=_KernelContext(),
        commit=_ForbiddenCommit(),
    )

    await kernel.turn(
        TurnRequest(
            session_key=snapshot.session_key,
            interaction_id="1777651204.0",
            actor_user_id=snapshot.owner_user_id,
            expected_revision=snapshot.revision,
            intent=Advance(),
        ),
        progress=_Progress(),
    )

    assert runner.calls[0][0].approvals == [approval]


async def test_produce_has_no_transcript_or_assistant_history_input() -> None:
    """Catches historical Slack prose contaminating a fresh planning run."""

    runner = _RecordedHarnessRunner()
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=_RecordedTmbx({"ok": True, "snapshot": {}}),
        constraint_reader=_RecordedConstraintReader([]),
        calendar_id="hugo.evers@gmail.com",
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
        harness_runner=runner,
    )
    slack_assistant_history = [
        ("assistant", "Yesterday you said no gym"),
        ("user", "it is vacation"),
    ]

    await planner.produce(_input_brief(), _Progress())

    serialized = runner.calls[0][0].model_dump_json()
    assert "Yesterday you said no gym" not in serialized
    assert "it is vacation" not in serialized
    assert slack_assistant_history  # the contamination exists outside the API seam


async def test_typed_current_session_fact_is_not_filtered_as_contamination() -> None:
    """Catches hygiene code deleting accepted typed facts based on their prose."""

    typed_fact = PlanningFact(
        fact_id="fact-vacation",
        kind=FactKind.REQUESTED_ACTIVITY,
        value={"note": "it is vacation"},
        source="user",
        source_interaction_id="1777651203.0",
    )
    runner = _RecordedHarnessRunner()
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=_RecordedTmbx({"ok": True, "snapshot": {}}),
        constraint_reader=_RecordedConstraintReader([]),
        calendar_id="hugo.evers@gmail.com",
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
        harness_runner=runner,
    )

    await planner.produce(_input_brief(facts=[typed_fact]), _Progress())

    assert "it is vacation" in runner.calls[0][0].model_dump_json()


@pytest.mark.parametrize(
    ("calendar", "constraints"),
    [
        (ReadUnavailable("private provider response"), []),
        ({"ok": True, "snapshot": {}}, RuntimeError("private memory path")),
        ({"ok": False, "message": "private calendar body"}, []),
    ],
)
async def test_missing_host_dependency_fails_typed_without_provider_text(
    calendar: dict[str, Any] | Exception,
    constraints: list[dict[str, Any]] | Exception,
) -> None:
    """Catches silently planning from empty or provider-authored context."""

    runner = _RecordedHarnessRunner()
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=_RecordedTmbx(calendar),
        constraint_reader=_RecordedConstraintReader(constraints),
        calendar_id="hugo.evers@gmail.com",
        clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
        harness_runner=runner,
    )

    with pytest.raises(DependencyUnavailable) as caught:
        await planner.produce(_input_brief(), _Progress())

    assert "private" not in str(caught.value)
    assert runner.calls == []


async def test_unavailable_constraint_reader_never_becomes_empty_context() -> None:
    reader = UnavailableConstraintReader()

    with pytest.raises(DependencyUnavailable):
        await reader.query_constraints(filters={"planned_day": "2026-08-29"}, limit=200)


async def test_runtime_builds_configured_kg_constraint_store(
    tmp_path, monkeypatch
) -> None:
    """Catches runtime planner reads bypassing the configured durable corpus."""

    db_path = tmp_path / "memory.db"
    ConstraintStore(str(db_path))
    monkeypatch.setattr(settings, "memory_db_path", str(db_path))

    store = await runtime_module._build_timeboxing_constraint_store()

    assert isinstance(store, ClientBackedDurableConstraintStore)


@pytest.mark.parametrize("configured", ["", "/does/not/exist/memory.db"])
async def test_runtime_preserves_missing_constraint_store_as_unavailable(
    configured: str, monkeypatch
) -> None:
    """Catches absent memory silently becoming an authoritative empty list."""

    monkeypatch.setattr(settings, "memory_db_path", configured)

    store = await runtime_module._build_timeboxing_constraint_store()

    with pytest.raises(DependencyUnavailable):
        await store.query_constraints(filters={"planned_day": "2026-08-29"}, limit=200)


@pytest.mark.parametrize("failure_kind", ["directory", "unreadable", "corrupt"])
async def test_runtime_classifies_unusable_existing_store_before_planning(
    failure_kind: str, tmp_path, monkeypatch
) -> None:
    """Catches an existing but unusable KG path claiming authoritative context."""

    db_path = tmp_path / "memory.db"
    original_bytes: bytes | None = None
    if failure_kind == "directory":
        db_path.mkdir()
    elif failure_kind == "unreadable":
        db_path.write_bytes(b"sqlite-placeholder")
        db_path.chmod(0)
    else:
        original_bytes = b"not-a-sqlite-store\x00private-provider-payload"
        db_path.write_bytes(original_bytes)
    monkeypatch.setattr(settings, "memory_db_path", str(db_path))

    store = await runtime_module._build_timeboxing_constraint_store()

    with pytest.raises(DependencyUnavailable) as caught:
        await store.query_constraints(
            filters={"planned_day": "2026-08-29"}, limit=200
        )
    assert "private-provider-payload" not in str(caught.value)
    if original_bytes is not None:
        assert db_path.read_bytes() == original_bytes


@pytest.mark.parametrize("blank", ["", "   "])
def test_planner_rejects_a_blank_calendar_id(blank: str) -> None:
    """Catches a blank identity satisfying the required-argument guard."""

    with pytest.raises(ValueError):
        DeepSeekTimeboxPlanner(
            tmbx_client=_RecordedTmbx({"ok": True, "snapshot": {}}),
            constraint_reader=_RecordedConstraintReader([]),
            calendar_id=blank,
            clock=lambda: datetime(2026, 8, 29, tzinfo=UTC),
            harness_runner=_RecordedHarnessRunner(),
        )


def _foreign_sqlite_store(path) -> None:
    """Write a valid SQLite database that is emphatically not the memory store."""

    connection = sqlite3.connect(str(path))
    try:
        connection.execute("CREATE TABLE unrelated_host_rows (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO unrelated_host_rows VALUES ('keep-me')")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("shape", ["empty_file", "foreign_sqlite"])
async def test_runtime_refuses_a_store_that_is_not_the_memory_corpus(
    shape: str, tmp_path, monkeypatch
) -> None:
    """Catches a foreign database answering as authoritative and being migrated into.

    A readable SQLite file is not the same thing as the memory corpus. Both of
    these shapes are stamped ``user_version = 0``, which the migration ladder
    treats as a fresh store and happily writes seven tables into.
    """

    db_path = tmp_path / "memory.db"
    if shape == "empty_file":
        db_path.write_bytes(b"")
    else:
        _foreign_sqlite_store(db_path)
    before = db_path.read_bytes()
    monkeypatch.setattr(settings, "memory_db_path", str(db_path))

    store = await runtime_module._build_timeboxing_constraint_store()

    with pytest.raises(DependencyUnavailable):
        await store.query_constraints(filters={"planned_day": "2026-08-29"}, limit=200)
    assert db_path.read_bytes() == before


async def test_runtime_refuses_a_store_newer_than_this_build(
    tmp_path, monkeypatch
) -> None:
    """Catches a future schema being read by a build that cannot understand it."""

    db_path = tmp_path / "memory.db"
    ConstraintStore(str(db_path))
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(settings, "memory_db_path", str(db_path))

    store = await runtime_module._build_timeboxing_constraint_store()

    with pytest.raises(DependencyUnavailable):
        await store.query_constraints(filters={"planned_day": "2026-08-29"}, limit=200)


async def test_runtime_still_accepts_a_pre_versioning_memory_store(
    tmp_path, monkeypatch
) -> None:
    """Catches the corpus check rejecting a legacy store the ladder can upgrade."""

    db_path = tmp_path / "memory.db"
    ConstraintStore(str(db_path))
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(settings, "memory_db_path", str(db_path))

    store = await runtime_module._build_timeboxing_constraint_store()

    assert isinstance(store, ClientBackedDurableConstraintStore)


def test_the_calendar_setting_binds_to_the_documented_variable(monkeypatch) -> None:
    """Catches a documented env var the settings model never reads.

    `Settings` has no `env_prefix`, so its fields bind to bare upper-case names.
    The FF_* variables in this repository are the separate ones read straight
    from `os.environ`. Documenting this one as FF_TIMEBOX_CALENDAR_ID left the
    planner silently unwired with the variable set exactly as written down —
    the failure is a route that reports itself inert while the operator can see
    their own configuration in the environment.
    """

    from fateforger.core.config import Settings

    monkeypatch.setenv("TIMEBOX_CALENDAR_ID", "someone@example.com")

    assert Settings().timebox_calendar_id == "someone@example.com"
