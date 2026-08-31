"""A third of the constraint block was empty lists and nulls.

Measured on a real session: 40 constraints, ~4,492 tokens, of which ~1,460 --
32% -- were `[]`, `{}` and `null`:

    applies_event_types  ~280    days_of_week  ~210    topics   ~150
    applies_stages       ~230    frame_slot    ~210
    scalar_params        ~220    windows       ~160

They are empty by design, not by accident. `_row_from_view` fills them in to
match the flat row shape reconciliation expects, and leaves applicability empty
deliberately -- `get_active_constraints` has already filtered to the day, and
restating a window would give a downstream filter a second chance to disagree
with the store.

So the internal shape is right. What was wrong is paying for it on every model
call: the brief is re-sent on every tool round-trip, and an absent key and an
empty list say the same thing to a reader.
"""

import json
from datetime import UTC, date, datetime

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    PlanningBrief,
    PlanningDay,
)
from fateforger.slack_bot.harness_bridge import _canonical_brief

FULL = {
    "uid": "abc",
    "name": "Work start time",
    "description": "The user starts working at 9:30.",
    "necessity": "must",
    "topics": [],
    "applies_stages": [],
    "windows": [],
    "scalar_params": {},
    "frame_slot": None,
}


def _brief() -> PlanningBrief:
    return PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 8, 31, tzinfo=UTC),
        locked_day=PlanningDay(
            date=date(2026, 8, 31), timezone="Europe/Amsterdam", iso_weekday=1,
            day_type=DayType.WORKING, classification_basis="calendar",
            lock_revision=1,
        ),
        facts=[], assumptions=[], current_artifacts=[], approvals=[],
        applicable_constraints=[FULL],
        calendar_snapshot={},
        target_artifact=ArtifactKind.SKELETON,
        readiness={},
        allowed_outputs=set(),
    )


def test_the_empty_fields_are_gone() -> None:
    rendered = _canonical_brief(_brief())
    for absent in ("topics", "applies_stages", "windows", "scalar_params",
                   "frame_slot"):
        assert absent not in rendered


def test_nothing_that_carries_meaning_is_lost() -> None:
    """Stripping must be provably lossless, not merely smaller."""

    payload = json.loads(_canonical_brief(_brief()))
    kept = payload["applicable_constraints"][0]
    for key, value in FULL.items():
        if value not in ([], {}, None, ""):
            assert kept[key] == value


def test_it_is_actually_smaller() -> None:
    rendered = _canonical_brief(_brief())
    assert len(rendered) < len(json.dumps(FULL)) + 400
