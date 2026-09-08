"""What the frame_slot instruction must and must not do.

This file used to assert that a fourteen-slug vocabulary was hardcoded, and
that the prompt repeated it. Both tests passed for as long as the list existed
and said nothing about whether it was right.

It was not. Measured against the anchors the memory server had learned from the
user's own words: ten of the fourteen named things he has never said
(`dog_walk`, `music_making`, `pre_gym_meal`, `sleep_target`, `work_window`),
while fourteen things he does do were missing (`fika`, `market_visits`,
`nature_reservation`, `prep_food`, `admin`, `finance`). A hand-typed model of
somebody's life is wrong in both directions at once.

What is worth asserting is the shape of the instruction, not its contents.
"""

from fateforger.agents.timeboxing import nlu
from fateforger.agents.timeboxing.nlu import CONSTRAINT_INTERPRETER_PROMPT


def test_no_frozen_slot_vocabulary_comes_back() -> None:
    """A slot is an anchor, and an anchor is discovered, not declared."""

    assert not hasattr(nlu, "FRAME_SLOT_CANONICAL_VALUES")


def test_the_prompt_names_no_closed_list_of_habits() -> None:
    """Catches somebody's daily routine reappearing as an enumeration.

    Illustrations are fine and useful -- the prompt still shows the *shape* of a
    slug. What must not return is a list presented as the set of slots that
    exist, because the model then reaches for the nearest member instead of
    naming what the user actually said.
    """

    assert "Canonical values" not in CONSTRAINT_INTERPRETER_PROMPT
    assert "pre_gym_meal" not in CONSTRAINT_INTERPRETER_PROMPT


def test_the_vocabulary_is_the_users_own() -> None:
    """Asserted on a fragment, because the prompt is hard-wrapped."""

    assert "own habits" in CONSTRAINT_INTERPRETER_PROMPT
    assert "not a fixed list" in CONSTRAINT_INTERPRETER_PROMPT


def test_a_recurring_routine_still_gets_a_slot() -> None:
    """The one rule that survives: a routine without a slot anchors nothing."""

    assert "null" in CONSTRAINT_INTERPRETER_PROMPT
    assert (
        "recurring" in CONSTRAINT_INTERPRETER_PROMPT
        or "routine" in CONSTRAINT_INTERPRETER_PROMPT
    )
