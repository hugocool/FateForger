"""The resolved work reaches the planner, by handle, with a use for it.

Task 4 decided *which* board rows a message names. This is the wiring on
either side of that judgement: the host reads the current sprint's Ready rows,
puts each resolved row into the material store, and files one `WORK_REFS`
fact; the brief then names the handles and says the one thing a planner may do
with one.

Three properties are worth more than the plumbing they sit on.

**The planner never sees a URL.** It has no use for one and must not learn to
write one -- the whole point of a handle is that a model copies ten characters
instead of transcribing a link.

**A board that could not be read is not a day with no work in it.** Those two
states produce the same absence of refs, and only one of them should change
how the planner reads the brief. So the failure says so in words, and the
ordinary "nothing was named" case stays completely silent.

**Nothing here looks at what the message says.** The judgement is
`resolve_work`'s; every model call in this file is a stub, and the tests
assert what was asked and what was done with the answer.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

import pytest

from fateforger.agents.tasks.board import TaskBoardUnavailable, TaskListing, TaskRow
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    InMemoryPlanningSessionRepository,
)
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactApproval,
    ArtifactDraft,
    ArtifactKind,
    DayType,
    FactKind,
    PlanningArtifact,
    PlanningBrief,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
)
from fateforger.agents.timeboxing.adaptive_timeboxing import TurnRequest
from fateforger.slack_bot import timeboxing_host
from fateforger.slack_bot.harness_bridge import _planning_obligation
from fateforger.slack_bot.timeboxing_host import HostPlanningContext
from fateforger.agents.timeboxing.work_refs import work_refs_on
from fateforger.slack_bot.timeboxing_host import (
    judge_ask,
    requested_work_text,
    work_refs_for_turn,
)

DAY = "2026-09-08"

FINANCE_URL = "https://www.notion.so/Verify-VPB-2024-aangifte-33628174"
DNS_URL = "https://www.notion.so/Move-the-DNS-33628174"


def _row(*, page_id: str, number: int, name: str, url: str) -> TaskRow:
    return TaskRow(
        page_id=page_id,
        number=number,
        name=name,
        status="In progress",
        ticket_status="Ready",
        summary="",
        dod="",
        url=url,
        last_edited="2026-09-07T10:00:00.000Z",
    )


FINANCE = _row(
    page_id="page-427",
    number=427,
    name="Verify VPB 2024 aangifte",
    url=FINANCE_URL,
)
DNS = _row(page_id="page-457", number=457, name="Move the DNS", url=DNS_URL)


class FakeBoard:
    """The board as `TaskBoard` answers, plus a record of how it was asked."""

    def __init__(self, rows: list[TaskRow]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    async def list_tasks(
        self, scope: str, *, limit: int = 25, cursor: str | None = None
    ) -> TaskListing:
        self.calls.append(scope)
        return TaskListing(scope=scope, tasks=list(self._rows))


class RefusingBoard:
    """A board that cannot answer, which is the case that must not block."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_tasks(
        self, scope: str, *, limit: int = 25, cursor: str | None = None
    ) -> TaskListing:
        self.calls.append(scope)
        raise TaskBoardUnavailable("no Notion token is configured")


class RecordingStore:
    """`MaterialStore.put` as the host reaches it, minus the process boundary."""

    def __init__(self) -> None:
        self.puts: list[dict[str, str]] = []

    async def __call__(
        self, *, source: str, external_id: str, url: str, label: str
    ) -> str:
        self.puts.append(
            {"source": source, "external_id": external_id, "url": url, "label": label}
        )
        return f"m{len(self.puts):010d}"


def _answering(page_ids: list[str]):
    """An `ask` that names those rows, recording the prompt it was given."""

    prompts: list[str] = []

    async def ask(prompt: str) -> str:
        prompts.append(prompt)
        ids = ", ".join(f'"{page_id}"' for page_id in page_ids)
        return f'{{"page_ids": [{ids}]}}'

    return ask, prompts


def _brief(
    target: ArtifactKind,
    *,
    facts: list[PlanningFact] | None = None,
    work_refs_unresolved: bool = False,
) -> PlanningBrief:
    return PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 9, 8, tzinfo=UTC),
        locked_day=PlanningDay(
            date=date(2026, 9, 8),
            timezone="Europe/Amsterdam",
            iso_weekday=2,
            day_type=DayType.WORKING,
            classification_basis="calendar",
            lock_revision=1,
        ),
        facts=facts or [],
        assumptions=[],
        current_artifacts=[],
        approvals=[],
        applicable_constraints=[],
        calendar_snapshot={},
        target_artifact=target,
        readiness={},
        allowed_outputs=set(),
        work_refs_unresolved=work_refs_unresolved,
    )


async def _resolved(page_ids: list[str], rows: list[TaskRow] | None = None):
    board = FakeBoard(rows if rows is not None else [FINANCE, DNS])
    store = RecordingStore()
    ask, prompts = _answering(page_ids)
    refs = await work_refs_for_turn(
        day=DAY,
        message="finish the next finance ticket in the first shallow work block",
        board=board,
        ask=ask,
        put_material=store,
    )
    return refs, board, store, prompts


# --- the fact ---------------------------------------------------------------


def test_the_fact_kind_exists() -> None:
    """A kind the host files and the brief reads has to be on the contract."""
    assert FactKind.WORK_REFS in set(FactKind)


async def test_a_resolved_row_becomes_a_fact_carrying_handle_label_and_number() -> None:
    refs, _board, store, _prompts = await _resolved(["page-427"])

    assert len(refs.facts) == 1
    fact = refs.facts[0]
    assert fact.kind is FactKind.WORK_REFS
    assert fact.source == "system"
    assert fact.value == [
        {"link": "m0000000001", "label": "Verify VPB 2024 aangifte", "task": 427}
    ]
    # The handle is the store's, not a name this host invented.
    assert store.puts[0]["external_id"] == "page-427"


async def test_every_resolved_row_is_put_into_the_material_store() -> None:
    """The handle on the brief has to name a row tmbx can resolve at apply."""
    _refs, _board, store, _prompts = await _resolved(["page-427", "page-457"])

    assert store.puts == [
        {
            "source": "notion",
            "external_id": "page-427",
            "url": FINANCE_URL,
            "label": "Verify VPB 2024 aangifte",
        },
        {
            "source": "notion",
            "external_id": "page-457",
            "url": DNS_URL,
            "label": "Move the DNS",
        },
    ]


async def test_the_scope_is_the_hosts_decision() -> None:
    """The current sprint's Ready rows are what fixes the meaning of "next"."""
    _refs, board, _store, _prompts = await _resolved(["page-427"])

    assert board.calls == ["current_sprint_ready"]


async def test_the_rows_reach_the_lookup_in_the_boards_own_order() -> None:
    """`build_prompt` tells the model the list is the board's ranking, so a
    caller that re-sorts tells it a falsehood and gets a wrong ticket back
    with no error to notice it by."""
    _refs, _board, _store, prompts = await _resolved(
        ["page-457"], rows=[DNS, FINANCE]
    )

    assert len(prompts) == 1
    assert prompts[0].index("page-457") < prompts[0].index("page-427")


# --- the brief --------------------------------------------------------------


async def test_the_brief_names_the_handle_and_what_to_do_with_it() -> None:
    refs, _board, _store, _prompts = await _resolved(["page-427"])
    text = _planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE, facts=refs.facts)
    )

    assert "m0000000001" in text
    assert "#427" in text
    assert "Verify VPB 2024 aangifte" in text
    # The one instruction: the handle goes on the op that places the block.
    assert "link" in text
    assert "update" in text


async def test_a_ticket_with_no_board_number_is_still_named() -> None:
    """`TaskRow.number` is optional, and "#None" on a brief is a bug the
    planner would faithfully copy onto a card."""
    unnumbered = PlanningFact(
        fact_id="work-refs:2026-09-08",
        kind=FactKind.WORK_REFS,
        value=[{"link": "mabc1234567", "label": "Verify VPB", "task": None}],
        source="system",
    )
    text = _planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE, facts=[unnumbered])
    )

    assert "mabc1234567 -- Verify VPB" in text
    assert "None" not in text


async def test_the_brief_never_shows_the_planner_a_url() -> None:
    """A planner that has seen one URL is a planner that will write one."""
    refs, _board, _store, _prompts = await _resolved(["page-427", "page-457"])
    text = _planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE, facts=refs.facts)
    )

    assert FINANCE_URL not in text
    assert DNS_URL not in text
    assert "notion.so" not in text


async def test_a_turn_that_resolves_nothing_is_silent() -> None:
    """The ordinary case: a message that names a topic, not a ticket."""
    board = FakeBoard([FINANCE, DNS])
    store = RecordingStore()
    ask, _prompts = _answering([])
    refs = await work_refs_for_turn(
        day=DAY,
        message="serious c2f work in the morning",
        board=board,
        ask=ask,
        put_material=store,
    )

    assert refs.facts == []
    assert refs.unresolved is False
    assert store.puts == []

    before = _planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    after = _planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE, facts=refs.facts)
    )
    assert after == before


# --- the board that could not be read ---------------------------------------


async def test_an_unreachable_board_files_no_fact_and_does_not_block(
    caplog: pytest.LogCaptureFixture,
) -> None:
    board = RefusingBoard()
    store = RecordingStore()
    ask, prompts = _answering(["page-427"])

    with caplog.at_level(logging.ERROR):
        refs = await work_refs_for_turn(
            day=DAY,
            message="finish the next finance ticket",
            board=board,
            ask=ask,
            put_material=store,
        )

    assert refs.facts == []
    assert refs.unresolved is True
    # Nothing was asked and nothing was stored: there were no rows to point at.
    assert prompts == []
    assert store.puts == []
    assert "work_board_unavailable" in caplog.text
    assert "TaskBoardUnavailable" in caplog.text


async def test_the_unreadable_board_says_so_in_the_brief() -> None:
    """So the planner reads the absence of refs as a failure to look rather
    than as a day with no work in it."""
    plain = _planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    unavailable = _planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE, work_refs_unresolved=True)
    )

    assert unavailable != plain
    assert "board" in unavailable


# --- every other failure takes the same path, under its own name ------------
#
# Ruled 2026-09-08: the user asked to plan a day, and whether the board timed
# out, the model named a ticket nobody showed it, or the store refused the
# write, the outcome for them is identical -- nobody knows which ticket they
# meant, so the day is planned unlinked. None of these may kill the turn, and
# each keeps its own event name because the four have different remedies.


class _HangingBoard:
    async def list_tasks(self, scope: str, *, limit: int = 25, cursor=None):
        await asyncio.sleep(60)
        raise AssertionError("the wait should have been bounded")


async def _lookup(
    *,
    board=None,
    ask=None,
    put_material=None,
    message: str = "finish the next finance ticket",
) -> object:
    if ask is None:
        ask, _prompts = _answering(["page-427"])
    return await work_refs_for_turn(
        day=DAY,
        message=message,
        board=board if board is not None else FakeBoard([FINANCE, DNS]),
        ask=ask,
        put_material=put_material if put_material is not None else RecordingStore(),
    )


async def test_a_board_outage_is_not_a_typed_board_error_and_is_still_caught(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`TaskBoard` raises `TaskBoardError` for an error envelope and a
    malformed page. Notion being slow or down arrives from inside the MCP
    client as something else entirely -- and that is the likeliest way this
    fails, so catching the typed one only would let the common case kill the
    turn."""

    class _Outage:
        async def list_tasks(self, scope: str, *, limit: int = 25, cursor=None):
            raise ConnectionError("connection refused")

    with caplog.at_level(logging.ERROR):
        refs = await _lookup(board=_Outage())

    assert refs.facts == []
    assert refs.unresolved is True
    assert "work_board_unavailable" in caplog.text
    assert "ConnectionError" in caplog.text


async def test_a_hallucinated_id_is_dropped_under_its_own_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`resolve_work` raises rather than acting on an id nobody showed it. The
    day is then planned unlinked: nothing acted on the bad id, and the brief
    says the work could not be resolved."""
    ask, _prompts = _answering(["page-999"])

    with caplog.at_level(logging.ERROR):
        refs = await _lookup(ask=ask)

    assert refs.facts == []
    assert refs.unresolved is True
    assert "work_lookup_hallucinated_id" in caplog.text
    assert "page-999" in caplog.text


async def test_a_transport_failure_on_the_judge_call_does_not_kill_the_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _refusing_ask(prompt: str) -> str:
        raise RuntimeError("openrouter said no")

    with caplog.at_level(logging.ERROR):
        refs = await _lookup(ask=_refusing_ask)

    assert refs.facts == []
    assert refs.unresolved is True
    assert "work_lookup_failed" in caplog.text


async def test_a_store_that_refuses_files_no_handle_it_cannot_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ref whose material was never stored is refused at plan_apply, one
    layer later. Better no ref at all, and the sentence on the brief."""

    async def _refusing_store(**_kwargs) -> str:
        raise RuntimeError("tmbx is not up")

    with caplog.at_level(logging.ERROR):
        refs = await _lookup(put_material=_refusing_store)

    assert refs.facts == []
    assert refs.unresolved is True
    assert "work_material_unstorable" in caplog.text


async def test_the_board_read_is_bounded(monkeypatch, caplog) -> None:
    """A planning turn that hangs is worse than one planned unlinked: the
    session cannot be continued and nobody is told why."""
    monkeypatch.setattr(timeboxing_host, "BOARD_TIMEOUT_S", 0.01)

    with caplog.at_level(logging.ERROR):
        refs = await _lookup(board=_HangingBoard())

    assert refs.unresolved is True
    assert "work_board_unavailable" in caplog.text
    assert "TimeoutError" in caplog.text


async def test_the_judgement_is_bounded(monkeypatch, caplog) -> None:
    monkeypatch.setattr(timeboxing_host, "LOOKUP_TIMEOUT_S", 0.01)

    async def _hanging_ask(prompt: str) -> str:
        await asyncio.sleep(60)
        raise AssertionError("the wait should have been bounded")

    with caplog.at_level(logging.ERROR):
        refs = await _lookup(ask=_hanging_ask)

    assert refs.unresolved is True
    assert "work_lookup_failed" in caplog.text
    assert "TimeoutError" in caplog.text


async def test_the_rows_are_stored_concurrently() -> None:
    """Two puts are independent, and this sits inside the latency of a turn
    somebody is watching. The barrier makes a sequential loop deadlock rather
    than merely being slower, which is the only way to assert this at all."""
    started = asyncio.Barrier(2)

    async def _barrier_store(**kwargs) -> str:
        await started.wait()
        return f"m-{kwargs['external_id']}"

    ask, _prompts = _answering(["page-427", "page-457"])
    refs = await asyncio.wait_for(
        _lookup(ask=ask, put_material=_barrier_store), timeout=2.0
    )

    assert [ref["link"] for ref in refs.facts[0].value] == [
        "m-page-427",
        "m-page-457",
    ]


# --- the seams the host wires -----------------------------------------------


def test_the_message_is_what_the_user_asked_the_day_to_hold() -> None:
    """`requested_activity` is the user's own words, filed by the interpreter.
    Nothing here reads them; they are handed to the judgement whole."""

    class _Snapshot:
        facts = [
            PlanningFact(
                fact_id="f1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="finish the next finance ticket",
                source="user",
            ),
            PlanningFact(
                fact_id="f2",
                kind=FactKind.DAY_FRAME,
                value={"wake": "07:00", "sleep": "23:00"},
                source="user",
            ),
            PlanningFact(
                fact_id="f3",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="gym at six",
                source="user",
            ),
        ]

    text = requested_work_text(_Snapshot())
    assert "finish the next finance ticket" in text
    assert "gym at six" in text
    assert "07:00" not in text


def test_a_session_that_asked_for_nothing_has_no_message() -> None:
    class _Snapshot:
        facts: list[PlanningFact] = []

    assert requested_work_text(_Snapshot()) == ""


async def test_the_lookup_is_asked_in_the_shape_the_eval_measured() -> None:
    """One user turn carrying the whole prompt, answered as a JSON object.

    `tests/integration/test_eval_work_lookup.py` measured the rates on that
    request shape and says so: a system/user split or a transport without
    `response_format` invalidates them. The judge client supplies the flash
    pin and `reasoning: minimal`; this pins the rest.
    """

    class _Result:
        content = '{"page_ids": []}'

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple, dict]] = []

        async def create(self, messages, **kwargs):
            self.calls.append((tuple(messages), kwargs))
            return _Result()

    client = _Client()
    answer = await judge_ask(client)("which of these?")

    assert answer == '{"page_ids": []}'
    (messages, kwargs), = client.calls
    assert len(messages) == 1
    assert messages[0].content == "which of these?"
    assert kwargs["json_output"] is True


def test_work_refs_on_reads_only_its_own_kind() -> None:
    other = PlanningFact(
        fact_id="required-blocks:2026-09-08",
        kind=FactKind.REQUIRED_BLOCKS,
        value={"slugs": ["planning"]},
        source="constraint_memory",
    )
    mine = PlanningFact(
        fact_id="work-refs:2026-09-08",
        kind=FactKind.WORK_REFS,
        value=[{"link": "mabc1234567", "label": "Verify VPB", "task": 427}],
        source="system",
    )

    assert work_refs_on([other, mine]) == [
        {"link": "mabc1234567", "label": "Verify VPB", "task": 427}
    ]


# --- reaching the material store across the process boundary ----------------
#
# The Slack host and tmbx are separate processes and the host must not open
# tmbx's database. So the handle is minted where the store is, behind one tool
# on the mount the host already talks to, and this is the client half.


class _Tool:
    def __init__(self, name: str, response: str) -> None:
        self.name = name
        self.response = response
        self.requests: list[dict] = []

    async def run_json(self, request: dict, _token: object) -> str:
        self.requests.append(request)
        return self.response


class _McpClient:
    def __init__(self, tools: list[_Tool]) -> None:
        self._tools = tools

    async def get_tools(self) -> list[_Tool]:
        return self._tools


def _tmbx(*tools: _Tool):
    from fateforger.slack_bot.tmbx_client import TmbxClient

    client = object.__new__(TmbxClient)
    client._client = _McpClient(list(tools))
    return client


async def test_the_host_stores_a_material_through_the_tmbx_mount() -> None:
    tool = _Tool("material_put", '{"ok": true, "link": "mabc1234567"}')

    handle = await _tmbx(tool).material_put(
        source="notion",
        external_id="page-427",
        url=FINANCE_URL,
        label="Verify VPB 2024 aangifte",
    )

    assert handle == "mabc1234567"
    assert tool.requests == [
        {
            "source": "notion",
            "external_id": "page-427",
            "url": FINANCE_URL,
            "label": "Verify VPB 2024 aangifte",
        }
    ]


async def test_a_refused_material_raises_rather_than_yielding_a_handle() -> None:
    """A handle the store does not hold is refused at plan_apply, one layer
    later and with nothing left to explain it."""
    from fateforger.slack_bot.tmbx_client import MaterialUnavailable

    tool = _Tool(
        "material_put",
        '{"ok": false, "reason": "malformed_input", "message": "needs an external_id"}',
    )

    with pytest.raises(MaterialUnavailable) as caught:
        await _tmbx(tool).material_put(
            source="notion", external_id="", url="u", label="L"
        )

    assert "malformed_input" in str(caught.value)


async def test_a_mount_without_the_tool_is_named_as_such() -> None:
    """An older tmbx is a deployment problem, not a day with no work in it."""
    from fateforger.slack_bot.tmbx_client import MaterialUnavailable

    with pytest.raises(MaterialUnavailable) as caught:
        await _tmbx(_Tool("plan_read", "{}")).material_put(
            source="notion", external_id="page-427", url="u", label="L"
        )

    assert "material_put" in str(caught.value)


# --- the join: the host's answer actually reaches the planner ---------------
#
# Everything above tests one side of a seam. This drives a real kernel turn
# through the real `HostPlanningContext` and reads the brief the planner was
# handed, because the two lines that carry the whole feature -- the
# `_work_refs` call and fact splice in the host, and
# `work_refs_unresolved=context.work_refs_unresolved` in `_build_brief` --
# belong to neither side and could both be deleted with everything above still
# green.


class _JudgeClient:
    """The judge model client, answering with the ids it was told to."""

    def __init__(self, page_ids: list[str]) -> None:
        self._page_ids = page_ids

    async def create(self, messages, **_kwargs):
        ids = ", ".join(f'"{page_id}"' for page_id in self._page_ids)

        class _Result:
            content = f'{{"page_ids": [{ids}]}}'

        return _Result()


class _ConstraintStore:
    async def query_constraints(self, *, filters, limit):
        return []

    async def count_suspended(self, day, day_type):
        return 0


class _KernelRuntime:
    def __init__(self, page_ids: list[str]) -> None:
        self.timeboxing_calendar_id = "cal"
        self.timeboxing_constraint_store = _ConstraintStore()
        self.timeboxing_judge_model_client = _JudgeClient(page_ids)


class _RecordingPlanner:
    def __init__(self) -> None:
        self.briefs: list[PlanningBrief] = []

    async def produce(self, brief: PlanningBrief, progress) -> PlanningResult:
        self.briefs.append(brief)
        return PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.VALIDATED_CANDIDATE,
                    payload={
                        "digest": "a" * 64,
                        "rendered": "17:00 Gym",
                        "snapshot": {"calendar_id": "cal", "day": "2026-09-08"},
                        "patch": {"ops": [{"op": "add", "h": "A"}]},
                    },
                    dependency_revisions={"skeleton": 1},
                )
            ]
        )


class _ForbiddenCommit:
    async def commit(self, candidate, *, digest):
        raise AssertionError("this turn commits nothing")


class _FakeTmbx:
    """tmbx as the candidate resolve uses it: a calendar read and a store."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def read(self, calendar_id, day):
        return {"ok": True, "calendar_id": calendar_id, "day": day, "blocks": 0}

    async def material_put(self, *, source, external_id, url, label):
        return f"m-{external_id}"


def _candidate_session() -> PlanningSessionSnapshot:
    """Past the skeleton gate, so one Advance reaches a candidate turn."""
    skeleton = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "## Tuesday"},
        dependency_revisions={"planning_day": 1},
    )
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1
        ),
        facts=[
            PlanningFact(
                fact_id="a1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="finish the next finance ticket",
                source="user",
            )
        ],
        artifacts=[skeleton],
        approvals=[
            ArtifactApproval(
                artifact_id=skeleton.artifact_id,
                artifact_revision=skeleton.revision,
                artifact_digest=skeleton.digest,
                actor_user_id="U1",
                session_revision=2,
            )
        ],
    )


async def _brief_from_a_candidate_turn(monkeypatch, board, page_ids) -> PlanningBrief:
    monkeypatch.setattr(
        "fateforger.agents.tasks.board.TaskBoard.from_settings",
        staticmethod(lambda: board),
    )
    monkeypatch.setattr("fateforger.slack_bot.tmbx_client.TmbxClient", _FakeTmbx)

    planner = _RecordingPlanner()
    kernel = AdaptiveTimeboxing(
        repository=InMemoryPlanningSessionRepository([_candidate_session()]),
        requirements=TimeboxRequirements(),
        planner=planner,
        context=HostPlanningContext(
            _KernelRuntime(page_ids),
            now=lambda: datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
        ),
        commit=_ForbiddenCommit(),
    )

    await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="i-1",
            actor_user_id="U1",
            expected_revision=3,
            intent=Advance(),
        ),
        progress=_Progress(),
    )
    assert planner.briefs, "the turn never reached the planner"
    return planner.briefs[-1]


class _Progress:
    async def emit(self, event: object) -> None:
        _ = event


async def test_the_resolved_handle_reaches_the_planners_brief(monkeypatch) -> None:
    brief = await _brief_from_a_candidate_turn(
        monkeypatch, FakeBoard([FINANCE, DNS]), ["page-427"]
    )

    assert work_refs_on(brief.facts) == [
        {"link": "m-page-427", "label": "Verify VPB 2024 aangifte", "task": 427}
    ]
    assert brief.work_refs_unresolved is False
    text = _planning_obligation(brief)
    assert "m-page-427" in text
    assert FINANCE_URL not in text


async def test_an_unresolvable_lookup_reaches_the_brief_as_a_sentence(
    monkeypatch,
) -> None:
    brief = await _brief_from_a_candidate_turn(
        monkeypatch, RefusingBoard(), ["page-427"]
    )

    assert work_refs_on(brief.facts) == []
    assert brief.work_refs_unresolved is True
    assert "could not be resolved" in _planning_obligation(brief)
