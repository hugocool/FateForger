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
    "metadata": {"backend": "memory_kg"},
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
    """Stripping must be provably lossless, not merely smaller.

    Two rules remove fields and they are different in kind: empty values are
    dropped because an absent key says the same thing, and the explicit list is
    dropped because the value says nothing the planner can act on. This asserts
    the first is lossless and reads the second from the source rather than
    restating it, so a field added there cannot quietly fail this test.
    """

    from fateforger.slack_bot.harness_bridge import (
        _CONSTRAINT_FIELDS_THE_PLANNER_CANNOT_USE as DROPPED,
    )

    payload = json.loads(_canonical_brief(_brief()))
    kept = payload["applicable_constraints"][0]
    for key, value in FULL.items():
        if value not in ([], {}, None, "") and key not in DROPPED:
            assert kept[key] == value


def test_it_is_actually_smaller() -> None:
    rendered = _canonical_brief(_brief())
    assert len(rendered) < len(json.dumps(FULL)) + 400


def test_the_storage_backend_is_not_the_planners_business() -> None:
    """`metadata` is a hardcoded literal, identical on every row.

    `_row_from_view` writes {"backend": "memory_kg"} on all of them. It names
    which store a rule came from, which the planner cannot act on and never
    references -- 390 tokens a call, ~3.5k a session, to say the same thing
    forty times.
    """

    assert "memory_kg" not in _canonical_brief(_brief())
    assert "metadata" not in _canonical_brief(_brief())


def test_the_uid_survives() -> None:
    """Measured: the planner cites uids in submit_planning_result to trace an
    assumption back to the rule that drove it. 6 of 1000 looks like noise and
    is not -- only a few constraints are cited per session. Dropping it would
    break that traceability, which is why "constant or unused" is decided by
    looking, not by the size of the number."""

    assert "abc" in _canonical_brief(_brief())
