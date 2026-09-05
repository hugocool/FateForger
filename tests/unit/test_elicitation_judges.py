"""The three Stage 1 judges and the orchestrator, with the model stubbed.

Every assertion is on what was sent and what the answer became -- never on
the model's words. The judges follow `DayFrameJudge`; the stub client is the
one `tests/unit/test_day_frame_on_record.py` uses.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CONCERNS, ROWS, CoverageMatrix
from fateforger.agents.timeboxing.elicitation_judges import (
    PLACEMENT_TARGETS,
    CoverageJudge,
    PlacementJudge,
    ProbeJudge,
    anchors_in,
    unanchored_in,
)
from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)

DAY = date(2026, 9, 8)


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


GYM = {"uid": "a-gym", "name": "gym"}
DINNER = {"uid": "a-din", "name": "dinner"}
ROWS_FIXTURE = [
    {"uid": "c-oats", "name": "Oats before gym", "description": "Eat oats two hours before the gym.", "necessity": "must", "anchors": [GYM]},
    {"uid": "c-run", "name": "Run at 18:00", "description": "Run at 18:00 when cooking dinner.", "necessity": "should", "anchors": [GYM, DINNER]},
    {"uid": "c-exit", "name": "Block exit criteria", "description": "Every block ends with a written exit criterion.", "necessity": "must", "anchors": []},
]


def test_placement_targets_are_the_concerns_plus_unplaced() -> None:
    assert PLACEMENT_TARGETS == (*(c.key for c in CONCERNS), "unplaced")
    assert "request" not in PLACEMENT_TARGETS


def test_anchors_in_groups_rows_by_anchor_with_two_example_names() -> None:
    anchors = anchors_in(ROWS_FIXTURE)
    by_uid = {a["uid"]: a for a in anchors}
    assert set(by_uid) == {"a-gym", "a-din"}
    assert by_uid["a-gym"]["name"] == "gym"
    assert by_uid["a-gym"]["example_rules"] == ["Oats before gym", "Run at 18:00"]
    assert by_uid["a-din"]["example_rules"] == ["Run at 18:00"]


def test_unanchored_in_returns_the_rules_with_no_anchor() -> None:
    assert [r["uid"] for r in unanchored_in(ROWS_FIXTURE)] == ["c-exit"]
    assert unanchored_in(ROWS_FIXTURE)[0]["description"].startswith("Every block")


@pytest.mark.asyncio
async def test_placement_maps_every_offered_uid_to_a_row() -> None:
    client = _SchemaOutputClient(
        {
            "anchors": [{"uid": "a-gym", "row": "body"}, {"uid": "a-din", "row": "fixed"}],
            "rules": [{"uid": "c-exit", "row": "method"}],
        }
    )
    placement = await PlacementJudge(client).place(
        anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
    )
    assert placement.anchors == {"a-gym": "body", "a-din": "fixed"}
    assert placement.rules == {"c-exit": "method"}
    sent = json.loads(client.calls[0][0][1].content)
    assert [a["uid"] for a in sent["anchors"]] == ["a-gym", "a-din"]
    assert [r["uid"] for r in sent["rules"]] == ["c-exit"]
    assert [c["key"] for c in sent["rows"]] == list(PLACEMENT_TARGETS)


@pytest.mark.asyncio
async def test_placement_refuses_a_uid_it_did_not_offer() -> None:
    client = _SchemaOutputClient(
        {"anchors": [{"uid": "a-gym", "row": "body"}, {"uid": "a-din", "row": "fixed"}, {"uid": "a-ghost", "row": "body"}], "rules": [{"uid": "c-exit", "row": "method"}]}
    )
    with pytest.raises(ValueError, match="a-ghost"):
        await PlacementJudge(client).place(
            anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_placement_refuses_to_leave_an_offered_uid_unplaced() -> None:
    client = _SchemaOutputClient({"anchors": [{"uid": "a-gym", "row": "body"}], "rules": []})
    with pytest.raises(ValueError, match="a-din"):
        await PlacementJudge(client).place(
            anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_placement_with_nothing_to_place_makes_no_call() -> None:
    client = _SchemaOutputClient()
    placement = await PlacementJudge(client).place(anchors=[], unanchored_rules=[], session_key="C1:1.0")
    assert placement.anchors == {} and placement.rules == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_classify_sends_the_row_the_criterion_and_names_only() -> None:
    client = _SchemaOutputClient({"status": "uncovered", "why": "no duration"})
    cell = CellRef(row="body", criterion="tacit_knowledge")
    state, why = await CoverageJudge(client).classify(
        cell=cell,
        rules=[{"name": "Oats before gym", "necessity": "must", "description": "SHOULD NOT BE SENT"}],
        stated=["gym at 18:00"],
        request="deep work in the morning, gym at 18:00",
        session_key="C1:1.0",
    )
    assert state == "uncovered"
    assert why == "no duration"
    sent = json.loads(client.calls[0][0][1].content)
    assert sent["row"]["key"] == "body"
    assert sent["criterion"]["key"] == "tacit_knowledge"
    assert sent["rules"] == [{"name": "Oats before gym", "necessity": "must"}]
    assert sent["stated"] == ["gym at 18:00"]
    assert sent["request"] == "deep work in the morning, gym at 18:00"


@pytest.mark.asyncio
async def test_classify_refuses_a_status_outside_the_schema() -> None:
    client = _SchemaOutputClient({"status": "maybe", "why": ""})
    with pytest.raises(ValueError):
        await CoverageJudge(client).classify(
            cell=CellRef(row="body", criterion="unclear"), rules=[], stated=[], request=None, session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_generate_returns_a_draft_with_host_minted_option_ids() -> None:
    client = _SchemaOutputClient(
        {"grounded": True, "question": "How long is the gym?", "why_needed": "to place it", "options": ["60 min", "90 min"]}
    )
    cell = CellRef(row="body", criterion="tacit_knowledge")
    draft = await ProbeJudge(client).generate(
        cell=cell,
        rules_full=[{"name": "Oats before gym", "necessity": "must", "description": "Eat oats two hours before the gym."}],
        conversation=["deep work in the morning, gym at 18:00"],
        request="deep work in the morning, gym at 18:00",
        session_key="C1:1.0",
    )
    assert draft is not None
    assert draft.cell_id == cell.id
    assert draft.question == "How long is the gym?"
    assert [o.option_id for o in draft.options] == ["elicit.body.tacit_knowledge:1", "elicit.body.tacit_knowledge:2"]
    assert [o.label for o in draft.options] == ["60 min", "90 min"]
    sent = json.loads(client.calls[0][0][1].content)
    assert sent["rules"][0]["description"].startswith("Eat oats")


@pytest.mark.asyncio
async def test_generate_may_return_nothing() -> None:
    client = _SchemaOutputClient({"grounded": False, "question": None, "why_needed": None, "options": []})
    draft = await ProbeJudge(client).generate(
        cell=CellRef(row="movement", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
    )
    assert draft is None


@pytest.mark.asyncio
async def test_generate_refuses_grounded_without_a_question() -> None:
    client = _SchemaOutputClient({"grounded": True, "question": None, "why_needed": None, "options": []})
    with pytest.raises(ValueError, match="grounded"):
        await ProbeJudge(client).generate(
            cell=CellRef(row="movement", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_generate_refuses_more_than_four_options() -> None:
    client = _SchemaOutputClient({"grounded": True, "question": "Which?", "why_needed": "w", "options": ["a", "b", "c", "d", "e"]})
    with pytest.raises(ValueError):
        await ProbeJudge(client).generate(
            cell=CellRef(row="body", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
        )
