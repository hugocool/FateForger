"""The fixture is three locked days, one request, and a label file whose cells
name real cells. Everything a spike reads is asserted here offline."""
from __future__ import annotations

from pathlib import Path

from fateforger.agents.timeboxing.elicitation import ALL_CELLS
from tests.fixtures.stage1.days import FIXTURE_DAYS, load_labels, snapshot_for

LABELS = Path(__file__).resolve().parents[1] / "fixtures" / "stage1" / "labels.toml"


def test_three_days_with_the_same_request() -> None:
    assert [d.key for d in FIXTURE_DAYS] == ["working_tuesday", "vacation_day", "sunday"]
    assert {d.request for d in FIXTURE_DAYS} == {"deep work in the morning, gym at 18:00"}
    assert [d.date.strftime("%A") for d in FIXTURE_DAYS] == ["Tuesday", "Wednesday", "Sunday"]


def test_a_fixture_snapshot_is_locked_and_carries_its_rows() -> None:
    rows = [{"uid": "c1", "name": "x", "necessity": "must", "anchors": []}]
    snapshot = snapshot_for(FIXTURE_DAYS[0], rows)
    assert snapshot.planning_day is not None
    assert snapshot.applicable_constraints == rows
    assert snapshot.stage1 == "open"


def test_labels_name_real_cells_and_every_day() -> None:
    labels = load_labels(LABELS)
    assert set(labels) == {d.key for d in FIXTURE_DAYS}
    valid = {cell.id for cell in ALL_CELLS}
    for day, gaps in labels.items():
        for gap in gaps:
            assert gap.cell in valid, (day, gap.cell)
