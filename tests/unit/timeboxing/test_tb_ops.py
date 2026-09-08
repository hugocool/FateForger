"""Unit tests for ``fateforger.agents.timeboxing.tb_ops``.

Covers:
- All 5 operation types: AddEvents, RemoveEvent, UpdateEvent, MoveEvent, ReplaceAll
- Index boundary checks (out-of-range raises IndexError)
- Discriminated union (de)serialization via ``TBOp`` / ``TBPatch``
- ``apply_tb_ops`` correctly produces a new validated TBPlan
- Multi-op patches (sequential application)
"""

from __future__ import annotations

from datetime import date

import pytest

from fateforger.agents.timeboxing.tb_models import ET, TBEvent, TBPlan
from fateforger.agents.timeboxing.tb_ops import (
    AddEvents,
    MoveEvent,
    RemoveEvent,
    ReplaceAll,
    TBPatch,
    UpdateEvent,
    apply_tb_ops,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _ev(name: str, dur: str = "PT1H", st: str = "08:00", t: str = "DW") -> TBEvent:
    """Quick event factory — all events use fixed_start for simplicity."""
    return TBEvent(n=name, d="", t=t, p={"a": "fs", "st": st, "dur": dur})


def _base_plan() -> TBPlan:
    """A 3-event plan used as a baseline for most tests."""
    return TBPlan(
        events=[
            _ev("Morning routine", st="07:00", dur="PT1H", t="H"),
            _ev("Deep work", st="08:00", dur="PT2H", t="DW"),
            _ev("Lunch", st="12:00", dur="PT1H", t="R"),
        ],
        date=date(2025, 1, 15),
        tz="Europe/Amsterdam",
    )


# ── ReplaceAll ───────────────────────────────────────────────────────────


class TestReplaceAll:
    """ReplaceAll should set the entire event list."""

    def test_replace_all(self) -> None:
        plan = _base_plan()
        new_events = [_ev("Only Event", st="09:00", dur="PT1H")]
        patch = TBPatch(ops=[ReplaceAll(events=new_events)])
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 1
        assert result.events[0].n == "Only Event"

    def test_replace_all_preserves_date(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[ReplaceAll(events=[_ev("A", st="09:00")])])
        result = apply_tb_ops(plan, patch)
        assert result.date == date(2025, 1, 15)
        assert result.tz == "Europe/Amsterdam"


# ── AddEvents ────────────────────────────────────────────────────────────


class TestAddEvents:
    """AddEvents appends or inserts events."""

    def test_append(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[AddEvents(events=[_ev("Walk", st="15:00", t="H")])])
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 4
        assert result.events[3].n == "Walk"

    def test_insert_after_index(self) -> None:
        plan = _base_plan()
        patch = TBPatch(
            ops=[AddEvents(events=[_ev("Snack", st="10:00", t="R")], after=0)]
        )
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 4
        assert result.events[1].n == "Snack"
        assert result.events[0].n == "Morning routine"
        assert result.events[2].n == "Deep work"

    def test_insert_multiple_events(self) -> None:
        plan = _base_plan()
        new = [
            _ev("Break1", st="10:00", t="R"),
            _ev("Break2", st="11:00", t="R"),
        ]
        patch = TBPatch(ops=[AddEvents(events=new, after=1)])
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 5
        assert result.events[2].n == "Break1"
        assert result.events[3].n == "Break2"


# ── RemoveEvent ──────────────────────────────────────────────────────────


class TestRemoveEvent:
    """RemoveEvent drops an event by index."""

    def test_remove_middle(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[RemoveEvent(i=1)])
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 2
        assert result.events[0].n == "Morning routine"
        assert result.events[1].n == "Lunch"

    def test_remove_first(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[RemoveEvent(i=0)])
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 2
        assert result.events[0].n == "Deep work"

    def test_remove_last(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[RemoveEvent(i=2)])
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 2

    def test_remove_out_of_range(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[RemoveEvent(i=10)])
        with pytest.raises(IndexError, match="remove"):
            apply_tb_ops(plan, patch)

    def test_remove_negative_index(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[RemoveEvent(i=-1)])
        with pytest.raises(IndexError, match="remove"):
            apply_tb_ops(plan, patch)


# ── UpdateEvent ──────────────────────────────────────────────────────────


class TestUpdateEvent:
    """UpdateEvent merges partial changes onto an existing event."""

    def test_update_name(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[UpdateEvent(i=0, n="New Name")])
        result = apply_tb_ops(plan, patch)
        assert result.events[0].n == "New Name"
        assert result.events[0].t == ET.H  # unchanged

    def test_update_type(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[UpdateEvent(i=1, t=ET.SW)])
        result = apply_tb_ops(plan, patch)
        assert result.events[1].t == ET.SW
        assert result.events[1].n == "Deep work"  # unchanged

    def test_update_description(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[UpdateEvent(i=0, d="Updated desc")])
        result = apply_tb_ops(plan, patch)
        assert result.events[0].d == "Updated desc"

    def test_update_timing(self) -> None:
        plan = _base_plan()
        from fateforger.agents.timeboxing.tb_models import FixedStart

        new_timing = FixedStart(st="09:30", dur="PT45M")
        patch = TBPatch(ops=[UpdateEvent(i=1, p=new_timing)])
        result = apply_tb_ops(plan, patch)
        assert result.events[1].p.a == "fs"
        assert result.events[1].p.st.hour == 9
        assert result.events[1].p.st.minute == 30

    def test_update_out_of_range(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[UpdateEvent(i=99, n="boom")])
        with pytest.raises(IndexError, match="update"):
            apply_tb_ops(plan, patch)

    def test_update_preserves_unset_fields(self) -> None:
        plan = _base_plan()
        original_timing = plan.events[0].p
        patch = TBPatch(ops=[UpdateEvent(i=0, n="Renamed")])
        result = apply_tb_ops(plan, patch)
        assert result.events[0].p == original_timing


# ── MoveEvent ────────────────────────────────────────────────────────────


class TestMoveEvent:
    """MoveEvent reorders events within the list."""

    def test_move_forward(self) -> None:
        plan = _base_plan()
        # Move first event to position 2
        patch = TBPatch(ops=[MoveEvent(fr=0, to=2)])
        result = apply_tb_ops(plan, patch)
        assert result.events[0].n == "Deep work"
        assert result.events[1].n == "Lunch"
        assert result.events[2].n == "Morning routine"

    def test_move_backward(self) -> None:
        plan = _base_plan()
        # Move last event to position 0
        patch = TBPatch(ops=[MoveEvent(fr=2, to=0)])
        result = apply_tb_ops(plan, patch)
        assert result.events[0].n == "Lunch"
        assert result.events[1].n == "Morning routine"
        assert result.events[2].n == "Deep work"

    def test_move_same_position(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[MoveEvent(fr=1, to=1)])
        result = apply_tb_ops(plan, patch)
        # Order unchanged
        assert [e.n for e in result.events] == ["Morning routine", "Deep work", "Lunch"]

    def test_move_out_of_range(self) -> None:
        plan = _base_plan()
        patch = TBPatch(ops=[MoveEvent(fr=10, to=0)])
        with pytest.raises(IndexError, match="move"):
            apply_tb_ops(plan, patch)


# ── Multi-op patches ────────────────────────────────────────────────────


class TestMultiOp:
    """Sequential application of multiple ops in a single patch."""

    def test_add_then_remove(self) -> None:
        plan = _base_plan()
        patch = TBPatch(
            ops=[
                AddEvents(events=[_ev("Temp", st="15:00", t="BU")]),  # idx 3
                RemoveEvent(i=3),  # remove the event we just added
            ]
        )
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 3  # net zero change

    def test_remove_then_add(self) -> None:
        plan = _base_plan()
        patch = TBPatch(
            ops=[
                RemoveEvent(i=1),  # remove "Deep work"
                AddEvents(events=[_ev("Focus time", st="08:00", dur="PT3H")], after=0),
            ]
        )
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 3
        assert result.events[1].n == "Focus time"

    def test_replace_then_update(self) -> None:
        plan = _base_plan()
        patch = TBPatch(
            ops=[
                ReplaceAll(events=[_ev("Solo", st="09:00", dur="PT1H")]),
                UpdateEvent(i=0, n="Renamed Solo"),
            ]
        )
        result = apply_tb_ops(plan, patch)
        assert len(result.events) == 1
        assert result.events[0].n == "Renamed Solo"


# ── Discriminated union serialization ────────────────────────────────────


class TestPatchSerialization:
    """TBPatch must round-trip through JSON for AutoGen tool calling."""

    def test_patch_round_trip(self) -> None:
        patch = TBPatch(
            ops=[
                AddEvents(events=[_ev("A", st="09:00")]),
                RemoveEvent(i=0),
                UpdateEvent(i=0, n="New"),
                MoveEvent(fr=0, to=1),
                ReplaceAll(events=[_ev("Z", st="08:00")]),
            ]
        )
        data = patch.model_dump(mode="json")
        restored = TBPatch.model_validate(data)
        assert len(restored.ops) == 5
        op_types = [op.op for op in restored.ops]
        assert op_types == ["ae", "re", "ue", "me", "ra"]

    def test_discriminator_resolves_correctly(self) -> None:
        """Raw JSON with ``op`` discriminator must parse to correct types."""
        raw = {
            "ops": [
                {
                    "op": "ae",
                    "events": [
                        {
                            "n": "X",
                            "d": "",
                            "t": "M",
                            "p": {"a": "fs", "st": "09:00", "dur": "PT1H"},
                        }
                    ],
                },
                {"op": "re", "i": 0},
                {"op": "ue", "i": 0, "n": "Y"},
                {"op": "me", "fr": 0, "to": 1},
                {
                    "op": "ra",
                    "events": [
                        {
                            "n": "Z",
                            "d": "",
                            "t": "DW",
                            "p": {"a": "fs", "st": "10:00", "dur": "PT2H"},
                        }
                    ],
                },
            ]
        }
        patch = TBPatch.model_validate(raw)
        assert isinstance(patch.ops[0], AddEvents)
        assert isinstance(patch.ops[1], RemoveEvent)
        assert isinstance(patch.ops[2], UpdateEvent)
        assert isinstance(patch.ops[3], MoveEvent)
        assert isinstance(patch.ops[4], ReplaceAll)

    def test_json_schema_has_discriminator(self) -> None:
        schema = TBPatch.model_json_schema()
        assert "$defs" in schema
        defs = schema["$defs"]
        assert "AddEvents" in defs
        assert "RemoveEvent" in defs

    def test_empty_ops_rejected(self) -> None:
        with pytest.raises(Exception):
            TBPatch(ops=[])

    def test_add_events_empty_list_rejected(self) -> None:
        with pytest.raises(Exception):
            AddEvents(events=[])


# ── Immutability ─────────────────────────────────────────────────────────


class TestImmutability:
    """apply_tb_ops must return a NEW plan, not mutate the original."""

    def test_original_unchanged_after_add(self) -> None:
        plan = _base_plan()
        original_count = len(plan.events)
        patch = TBPatch(ops=[AddEvents(events=[_ev("Extra", st="15:00")])])
        result = apply_tb_ops(plan, patch)
        assert len(plan.events) == original_count
        assert len(result.events) == original_count + 1

    def test_original_unchanged_after_remove(self) -> None:
        plan = _base_plan()
        original_names = [e.n for e in plan.events]
        patch = TBPatch(ops=[RemoveEvent(i=0)])
        apply_tb_ops(plan, patch)
        assert [e.n for e in plan.events] == original_names
