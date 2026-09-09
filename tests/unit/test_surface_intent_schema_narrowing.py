"""A state's schema carries `decision` and the fields its decisions can fill.

Strict structured output requires every property to be emitted, so a field the
state cannot use is a null the model must produce on every call -- 42% of
answers in the 2026-09-06 bench were a decision and six nulls.
"""

from __future__ import annotations

from typing import Literal, get_args

import pytest

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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


@pytest.mark.parametrize(
    "base",
    [InterpretedTimeboxTurn, InterpretedPlanningTurn, InterpretedSettledPlanningTurn],
    ids=lambda base: base.__name__,
)
def test_every_field_is_claimed_by_some_decision(base: type[BaseModel]) -> None:
    """The guard: a field no decision claims would be silently dropped from
    every state, and a decision that gains a field must claim it here.

    Every base `narrow_schema` is asked to narrow, not just timeboxing's: the
    planning card binds its own two schemas through the same table, so a new
    field on a planning turn would be dropped from every planning state with
    nothing failing unless this runs over those too."""
    claimed = {"decision"} | {f for fields in _FIELDS_BY_DECISION.values() for f in fields}
    assert set(base.model_fields) <= claimed


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
    # Every decision the base declares: nothing to drop from either the field
    # set or the `decision` Literal, so the base itself comes back.
    every = get_args(InterpretedTimeboxTurn.model_fields["decision"].annotation)
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


def test_a_base_carrying_a_validator_is_refused_not_silently_stripped() -> None:
    """The rebuild has no `__base__`, so a validator would vanish unremarked.

    `ArtifactActionMeta` already enforces "this decision requires that field"
    with exactly this shape, so one arriving on a turn schema is a plausible
    next change -- and losing it would let a malformed turn reach the binder.
    """

    class _TurnWithARule(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        decision: Literal["provide_facts", "deny"]
        facts: list[str] = Field(default_factory=list)
        assumption_id: str | None = None

        @model_validator(mode="after")
        def denying_names_its_assumption(self) -> "_TurnWithARule":
            if self.decision == "deny" and self.assumption_id is None:
                raise ValueError("deny needs an assumption")
            return self

    # It refuses on the narrowing path...
    with pytest.raises(TypeError, match="denying_names_its_assumption"):
        narrow_schema(_TurnWithARule, (), allowed_decisions=("provide_facts",))
    # ...and the rule it would have dropped is a real one.
    with pytest.raises(ValidationError):
        _TurnWithARule(decision="deny")


def test_narrowing_nothing_away_still_never_touches_a_validator() -> None:
    """The guard sits on the rebuild, not on the call: a state that drops no
    field and no decision returns the base untouched, validator intact."""

    class _Turn(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        decision: Literal["none"]

        @model_validator(mode="after")
        def always_fine(self) -> "_Turn":
            return self

    assert narrow_schema(_Turn, (), allowed_decisions=("none",)) is _Turn


def test_the_schema_offers_only_the_decisions_the_state_allows() -> None:
    """A schema advertising `revise` at the date stage asks the model for a
    decision it carries no field to express."""

    narrowed = narrow_schema(
        InterpretedTimeboxTurn, (), allowed_decisions=("confirm_planning_day", "cancel")
    )
    assert get_args(narrowed.model_fields["decision"].annotation) == (
        "confirm_planning_day",
        "cancel",
    )
    with pytest.raises(ValidationError):
        narrowed(decision="revise")


def test_the_allowed_decisions_are_taken_verbatim() -> None:
    """Exactly the state's own set, in its own order -- not an intersection
    with what the base happened to declare."""

    allowed = ("confirm_planning_day", "cancel", "question")
    narrowed = narrow_schema(InterpretedTimeboxTurn, (), allowed_decisions=allowed)
    assert get_args(narrowed.model_fields["decision"].annotation) == allowed


def test_a_state_with_options_offers_choose_option_and_nothing_else() -> None:
    narrowed = narrow_schema(
        InterpretedTimeboxTurn,
        (BlockerOption(option_id="opt_a", label="A", effect="does a"),),
        allowed_decisions=("provide_facts", "cancel", CHOOSE_OPTION),
    )
    assert get_args(narrowed.model_fields["decision"].annotation) == (
        "provide_facts",
        "cancel",
        CHOOSE_OPTION,
    )
    assert set(narrowed.model_fields) == {"decision", "facts", "option_id"}
