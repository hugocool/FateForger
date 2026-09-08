"""Unit tests for ``fateforger.agents.timeboxing.tb_models``.

Covers:
- Discriminated union (de)serialization for all 4 Timing variants
- TBEvent validation (BG must have fixed timing)
- TBPlan validation (chain must have an anchor)
- Time resolution: forward pass (ap, fs, fw), backward pass (bn), overlap detection
- ET_COLOR_MAP helpers
- JSON schema round-trip (confirms LLM can produce valid payloads)
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from fateforger.agents.timeboxing.tb_models import (
    ET,
    ET_COLOR_MAP,
    AfterPrev,
    BeforeNext,
    FixedStart,
    FixedWindow,
    TBEvent,
    TBPlan,
    gcal_color_to_et,
)

# ── Timing variant construction ──────────────────────────────────────────


class TestTimingVariants:
    """Test constructing each Timing variant from raw values and dicts."""

    def test_after_prev_from_iso(self) -> None:
        ap = AfterPrev(dur="PT30M")
        assert ap.dur == timedelta(minutes=30)
        assert ap.a == "ap"

    def test_before_next_from_iso(self) -> None:
        bn = BeforeNext(dur="PT1H")
        assert bn.dur == timedelta(hours=1)
        assert bn.a == "bn"

    def test_fixed_start_from_str(self) -> None:
        fs = FixedStart(st="09:00", dur="PT45M")
        assert fs.st == time(9, 0)
        assert fs.dur == timedelta(minutes=45)

    def test_fixed_window_from_str(self) -> None:
        fw = FixedWindow(st="10:00", et="11:30")
        assert fw.st == time(10, 0)
        assert fw.et == time(11, 30)

    def test_fixed_start_from_native(self) -> None:
        fs = FixedStart(st=time(8, 30), dur=timedelta(hours=1))
        assert fs.st == time(8, 30)

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            AfterPrev(dur="PT30M", extra="nope")


# ── TBEvent validation ───────────────────────────────────────────────────


class TestTBEvent:
    """Test TBEvent creation and validators."""

    def test_basic_event(self) -> None:
        ev = TBEvent(
            n="Standup", d="Daily", t="M", p={"a": "fs", "st": "09:00", "dur": "PT15M"}
        )
        assert ev.t == ET.M
        assert ev.n == "Standup"

    def test_bg_requires_fixed_timing(self) -> None:
        """BG events with after_previous should be rejected."""
        with pytest.raises(ValueError, match="Background events"):
            TBEvent(n="BGTask", d="", t="BG", p={"a": "ap", "dur": "PT30M"})

    def test_bg_with_fixed_window_ok(self) -> None:
        ev = TBEvent(
            n="BGTask", d="", t="BG", p={"a": "fw", "st": "09:00", "et": "17:00"}
        )
        assert ev.t == ET.BG

    def test_bg_with_fixed_start_ok(self) -> None:
        ev = TBEvent(
            n="BGTask", d="", t="BG", p={"a": "fs", "st": "09:00", "dur": "PT8H"}
        )
        assert ev.t == ET.BG

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):
            TBEvent(n="X", d="", t="M", p={"a": "ap", "dur": "PT30M"}, extra="bad")


# ── TBPlan validation ────────────────────────────────────────────────────


class TestTBPlanValidation:
    """Test TBPlan model validators."""

    def test_empty_plan_ok(self) -> None:
        plan = TBPlan(events=[])
        assert plan.events == []

    def test_chain_needs_anchor(self) -> None:
        """A chain of only after_previous events is invalid (no time anchor)."""
        with pytest.raises(ValueError, match="anchor"):
            TBPlan(
                events=[
                    TBEvent(n="A", d="", t="DW", p={"a": "ap", "dur": "PT1H"}),
                ]
            )

    def test_chain_with_fixed_start_anchor(self) -> None:
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Start",
                    d="",
                    t="PR",
                    p={"a": "fs", "st": "08:00", "dur": "PT30M"},
                ),
                TBEvent(n="Work", d="", t="DW", p={"a": "ap", "dur": "PT2H"}),
            ]
        )
        assert len(plan.events) == 2

    def test_bg_only_plan_no_anchor_needed(self) -> None:
        """BG events are excluded from the chain anchor check."""
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Music", d="", t="BG", p={"a": "fw", "st": "08:00", "et": "17:00"}
                ),
            ]
        )
        assert len(plan.events) == 1


# ── Time resolution ──────────────────────────────────────────────────────


class TestResolveTime:
    """Test ``TBPlan.resolve_times()`` — the core scheduling logic."""

    @pytest.fixture()
    def day(self) -> date:
        return date(2025, 1, 15)

    def test_single_fixed_start(self, day: date) -> None:
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Morning",
                    d="",
                    t="PR",
                    p={"a": "fs", "st": "08:00", "dur": "PT1H"},
                )
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert len(resolved) == 1
        assert resolved[0]["start_time"] == time(8, 0)
        assert resolved[0]["end_time"] == time(9, 0)

    def test_after_prev_chain(self, day: date) -> None:
        plan = TBPlan(
            events=[
                TBEvent(
                    n="A", d="", t="PR", p={"a": "fs", "st": "07:00", "dur": "PT1H"}
                ),
                TBEvent(n="B", d="", t="DW", p={"a": "ap", "dur": "PT30M"}),
                TBEvent(n="C", d="", t="SW", p={"a": "ap", "dur": "PT45M"}),
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert resolved[0]["end_time"] == time(8, 0)
        assert resolved[1]["start_time"] == time(8, 0)
        assert resolved[1]["end_time"] == time(8, 30)
        assert resolved[2]["start_time"] == time(8, 30)
        assert resolved[2]["end_time"] == time(9, 15)

    def test_fixed_window(self, day: date) -> None:
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Meeting",
                    d="",
                    t="M",
                    p={"a": "fw", "st": "14:00", "et": "15:30"},
                )
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert resolved[0]["start_time"] == time(14, 0)
        assert resolved[0]["end_time"] == time(15, 30)
        assert resolved[0]["duration"] == timedelta(hours=1, minutes=30)

    def test_before_next(self, day: date) -> None:
        """before_next event should end exactly when the next event starts."""
        plan = TBPlan(
            events=[
                TBEvent(n="Prep", d="", t="SW", p={"a": "bn", "dur": "PT30M"}),
                TBEvent(
                    n="Meeting",
                    d="",
                    t="M",
                    p={"a": "fs", "st": "10:00", "dur": "PT1H"},
                ),
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert resolved[0]["start_time"] == time(9, 30)
        assert resolved[0]["end_time"] == time(10, 0)
        assert resolved[1]["start_time"] == time(10, 0)

    def test_before_next_no_successor_raises(self, day: date) -> None:
        """bn as the last event has no successor → resolve_times error."""
        # Bypass chain_must_be_anchored validator (bn alone has no anchor)
        plan = TBPlan.__new__(TBPlan)
        object.__setattr__(
            plan,
            "events",
            [
                TBEvent(n="Dangling", d="", t="SW", p={"a": "bn", "dur": "PT30M"}),
            ],
        )
        object.__setattr__(plan, "date", day)
        object.__setattr__(plan, "tz", "Europe/Amsterdam")
        with pytest.raises(ValueError, match="no following"):
            plan.resolve_times()

    def test_after_prev_no_predecessor_raises(self, day: date) -> None:
        """after_previous as first event has no predecessor → error."""
        plan = TBPlan.__new__(TBPlan)
        # Bypass the chain_must_be_anchored validator for this edge case
        object.__setattr__(
            plan,
            "events",
            [
                TBEvent(n="Orphan", d="", t="DW", p={"a": "ap", "dur": "PT1H"}),
            ],
        )
        object.__setattr__(plan, "date", day)
        object.__setattr__(plan, "tz", "Europe/Amsterdam")
        with pytest.raises(ValueError, match="no preceding"):
            plan.resolve_times()

    def test_overlap_detected(self, day: date) -> None:
        """Two fixed events that overlap should raise."""
        plan = TBPlan(
            events=[
                TBEvent(
                    n="A", d="", t="M", p={"a": "fs", "st": "10:00", "dur": "PT1H"}
                ),
                TBEvent(
                    n="B", d="", t="M", p={"a": "fs", "st": "10:30", "dur": "PT1H"}
                ),
            ],
            date=day,
        )
        with pytest.raises(ValueError, match="Overlap"):
            plan.resolve_times()

    def test_bg_events_excluded_from_overlap(self, day: date) -> None:
        """BG events should not trigger overlap errors with chain events."""
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Music", d="", t="BG", p={"a": "fw", "st": "08:00", "et": "17:00"}
                ),
                TBEvent(
                    n="Work", d="", t="DW", p={"a": "fs", "st": "09:00", "dur": "PT2H"}
                ),
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert len(resolved) == 2

    def test_mixed_timing_plan(self, day: date) -> None:
        """Complex plan with multiple timing types resolves correctly."""
        plan = TBPlan(
            events=[
                TBEvent(
                    n="Morning routine",
                    d="",
                    t="H",
                    p={"a": "fs", "st": "07:00", "dur": "PT1H"},
                ),
                TBEvent(n="Commute", d="", t="C", p={"a": "ap", "dur": "PT30M"}),
                TBEvent(
                    n="Prep for standup", d="", t="SW", p={"a": "bn", "dur": "PT15M"}
                ),
                TBEvent(
                    n="Standup",
                    d="",
                    t="M",
                    p={"a": "fs", "st": "09:00", "dur": "PT15M"},
                ),
                TBEvent(n="Deep work", d="", t="DW", p={"a": "ap", "dur": "PT3H"}),
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert len(resolved) == 5
        # Morning routine: 07:00-08:00
        assert resolved[0]["start_time"] == time(7, 0)
        assert resolved[0]["end_time"] == time(8, 0)
        # Commute: 08:00-08:30
        assert resolved[1]["start_time"] == time(8, 0)
        assert resolved[1]["end_time"] == time(8, 30)
        # Prep: 08:45-09:00 (before_next from standup at 09:00)
        assert resolved[2]["start_time"] == time(8, 45)
        assert resolved[2]["end_time"] == time(9, 0)
        # Standup: 09:00-09:15
        assert resolved[3]["start_time"] == time(9, 0)
        assert resolved[3]["end_time"] == time(9, 15)
        # Deep work: 09:15-12:15
        assert resolved[4]["start_time"] == time(9, 15)
        assert resolved[4]["end_time"] == time(12, 15)

    def test_resolve_preserves_index(self, day: date) -> None:
        plan = TBPlan(
            events=[
                TBEvent(
                    n="A", d="", t="PR", p={"a": "fs", "st": "08:00", "dur": "PT1H"}
                ),
                TBEvent(n="B", d="", t="DW", p={"a": "ap", "dur": "PT2H"}),
            ],
            date=day,
        )
        resolved = plan.resolve_times()
        assert resolved[0]["index"] == 0
        assert resolved[1]["index"] == 1


# ── ET_COLOR_MAP helpers ─────────────────────────────────────────────────


class TestColorMapping:
    """Test ET ↔ GCal colorId mapping."""

    def test_all_et_values_have_color(self) -> None:
        for et in ET:
            assert et.value in ET_COLOR_MAP, f"Missing color mapping for {et}"

    def test_gcal_color_to_et_known(self) -> None:
        assert gcal_color_to_et("9") == ET.DW
        assert gcal_color_to_et("6") == ET.M

    def test_gcal_color_to_et_unknown_defaults(self) -> None:
        assert gcal_color_to_et("99") == ET.M
        assert gcal_color_to_et(None) == ET.M


# ── JSON schema round-trip ────────────────────────────────────────────────


class TestJsonRoundTrip:
    """Test that models serialize/deserialize cleanly (LLM schema contract)."""

    def test_tb_event_round_trip(self) -> None:
        ev = TBEvent(
            n="Test", d="desc", t="DW", p={"a": "fs", "st": "09:00", "dur": "PT1H"}
        )
        data = ev.model_dump(mode="json")
        restored = TBEvent.model_validate(data)
        assert restored.n == ev.n
        assert restored.t == ev.t
        assert restored.p.a == "fs"

    def test_tb_plan_round_trip(self) -> None:
        plan = TBPlan(
            events=[
                TBEvent(
                    n="A", d="", t="PR", p={"a": "fs", "st": "08:00", "dur": "PT30M"}
                ),
                TBEvent(n="B", d="", t="DW", p={"a": "ap", "dur": "PT2H"}),
            ],
            date=date(2025, 3, 1),
            tz="Europe/Amsterdam",
        )
        data = plan.model_dump(mode="json")
        restored = TBPlan.model_validate(data)
        assert len(restored.events) == 2
        assert restored.date == date(2025, 3, 1)
        assert restored.events[1].p.a == "ap"

    def test_discriminator_in_json_schema(self) -> None:
        """The JSON schema must expose the discriminator for strict tool calling."""
        schema = TBEvent.model_json_schema()
        # The schema should reference all timing variants
        defs = schema.get("$defs", {})
        timing_names = {d for d in defs}
        assert "AfterPrev" in timing_names
        assert "FixedWindow" in timing_names

    def test_tb_plan_json_schema_exists(self) -> None:
        schema = TBPlan.model_json_schema()
        assert "properties" in schema
        assert "events" in schema["properties"]
