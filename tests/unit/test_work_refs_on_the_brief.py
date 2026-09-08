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

import logging
from datetime import UTC, date, datetime

import pytest

from fateforger.agents.tasks.board import TaskBoardUnavailable, TaskListing, TaskRow
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    FactKind,
    PlanningBrief,
    PlanningDay,
    PlanningFact,
)
from fateforger.slack_bot.harness_bridge import _planning_obligation
from fateforger.slack_bot.timeboxing_host import (
    judge_ask,
    requested_work_text,
    work_refs_for_turn,
    work_refs_on,
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
    work_board_unavailable: bool = False,
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
        work_board_unavailable=work_board_unavailable,
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
    assert refs.board_unavailable is False
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
    assert refs.board_unavailable is True
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
        _brief(ArtifactKind.VALIDATED_CANDIDATE, work_board_unavailable=True)
    )

    assert unavailable != plain
    assert "board" in unavailable


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
