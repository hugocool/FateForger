"""A state's schema carries `decision` and the fields its decisions can fill.

Strict structured output requires every property to be emitted, so a field the
state cannot use is a null the model must produce on every call -- 42% of
answers in the 2026-09-06 bench were a decision and six nulls.
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.slack_bot.planning_surface import (
    ADD_OPTION_ID,
    InterpretedPlanningTurn,
    InterpretedSettledPlanningTurn,
)
from fateforger.slack_bot.surface_intents import (
    CHOOSE_OPTION,
    _FIELDS_BY_DECISION,
    narrow_schema,
)
from fateforger.slack_bot.timeboxing_intents import InterpretedTimeboxTurn


def _fields(allowed: tuple[str, ...]) -> set[str]:
    return set(narrow_schema(InterpretedTimeboxTurn, (), allowed_decisions=allowed).model_fields)


def test_a_state_that_can_only_classify_carries_one_field() -> None:
    assert _fields(("start", "question", "cancel")) == {"decision"}


def test_a_state_that_extracts_carries_facts() -> None:
    assert _fields(("provide_facts", "question")) == {"decision", "facts"}


def test_revise_carries_its_instruction_and_facts() -> None:
    assert _fields(("revise", "question")) == {"decision", "facts", "revision_instruction"}


def test_confirming_a_day_carries_the_day_fields_only() -> None:
    assert _fields(("confirm_planning_day", "cancel")) == {"decision", "day_type", "day_offset"}


def test_steering_carries_the_uid_and_the_suspension_fact() -> None:
    assert _fields(("steer_not_today", "restore")) == {"decision", "facts", "constraint_uid"}


def test_denying_carries_the_assumption_id() -> None:
    assert _fields(("deny", "advance")) == {"decision", "assumption_id"}


def test_omitting_the_argument_narrows_nothing_away() -> None:
    # Every existing caller passes two arguments; none may lose a field.
    assert set(narrow_schema(InterpretedTimeboxTurn, ()).model_fields) == set(
        InterpretedTimeboxTurn.model_fields
    )


def test_every_field_is_claimed_by_some_decision() -> None:
    """The guard: a field no decision claims would be silently dropped from
    every state, and a decision that gains a field must claim it here."""
    from fateforger.slack_bot.surface_intents import _FIELDS_BY_DECISION

    claimed = {"decision"} | {f for fields in _FIELDS_BY_DECISION.values() for f in fields}
    assert set(InterpretedTimeboxTurn.model_fields) <= claimed


def test_the_day_offset_bounds_survive_the_rebuild() -> None:
    """A lost bound is silent: a plausible offset lands the plan a year away."""
    narrowed = narrow_schema(
        InterpretedTimeboxTurn, (), allowed_decisions=("confirm_planning_day",)
    )
    with pytest.raises(ValidationError):
        narrowed(decision="confirm_planning_day", day_offset=99)
    assert narrowed(decision="confirm_planning_day", day_offset=3).day_offset == 3


def test_the_narrowed_schema_is_as_strict_as_the_base() -> None:
    narrowed = narrow_schema(
        InterpretedTimeboxTurn, (), allowed_decisions=("provide_facts",)
    )
    assert narrowed.model_config == InterpretedTimeboxTurn.model_config
    with pytest.raises(ValidationError):
        narrowed(decision="provide_facts", assumption_id="a")


def test_a_state_that_retains_everything_reads_the_same_json() -> None:
    """The rebuild is a removal, not a re-declaration."""
    every = tuple(_FIELDS_BY_DECISION)
    narrowed = narrow_schema(InterpretedTimeboxTurn, (), allowed_decisions=every)
    assert narrowed is InterpretedTimeboxTurn
    payload = (
        '{"decision":"revise","facts":[],"revision_instruction":"move it later",'
        '"day_type":null,"day_offset":null,"constraint_uid":null,'
        '"assumption_id":null}'
    )
    assert narrowed.model_validate_json(payload).revision_instruction == "move it later"


def test_offering_options_keeps_the_option_id() -> None:
    """`option_id` is minted by the option narrowing, not declared on the base;
    the field pass must run after it and must see CHOOSE_OPTION as allowed."""
    narrowed = narrow_schema(
        InterpretedTimeboxTurn,
        (BlockerOption(option_id="opt_a", label="A", effect="does a"),),
        allowed_decisions=("provide_facts", "cancel", CHOOSE_OPTION),
    )
    assert set(narrowed.model_fields) == {"decision", "facts", "option_id"}
    assert narrowed(decision=CHOOSE_OPTION, option_id="opt_a").option_id == "opt_a"


def test_the_planning_surface_keeps_its_time_and_its_validator() -> None:
    """A different base declares `selected_time` and no `facts`; a map entry
    naming a field this base lacks is a no-op, not an error."""
    narrowed = narrow_schema(
        InterpretedPlanningTurn,
        (BlockerOption(option_id=ADD_OPTION_ID, label="Add", effect="adds it"),),
        allowed_decisions=("update_time", "update_time_and_add", "none", CHOOSE_OPTION),
    )
    assert set(narrowed.model_fields) == {"decision", "selected_time", "option_id"}
    # Clock's AfterValidator normalises the model's own output; losing it would
    # hand the host "8:30" where it splits on ":" and expects two digits.
    read = narrowed(decision="update_time", selected_time="8:30", option_id=None)
    assert read.selected_time == "08:30"


def test_a_settled_planning_card_carries_only_its_decision() -> None:
    narrowed = narrow_schema(
        InterpretedSettledPlanningTurn, (), allowed_decisions=("none",)
    )
    assert set(narrowed.model_fields) == {"decision"}
