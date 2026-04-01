"""frame_slot prompt must expose the canonical seed list and open-vocab instruction."""
from fateforger.agents.timeboxing.nlu import (
    CONSTRAINT_INTERPRETER_PROMPT,
    FRAME_SLOT_CANONICAL_VALUES,
)


def test_canonical_seed_list_exported():
    required = {
        "morning_ritual", "commute_out", "work_window", "lunch_break",
        "commute_back", "gym", "pre_gym_meal", "dinner", "evening_wind_down",
        "music_making", "shutdown", "dog_walk", "sleep_target", "reading",
    }
    assert required.issubset(FRAME_SLOT_CANONICAL_VALUES)


def test_prompt_contains_seed_values():
    for slug in FRAME_SLOT_CANONICAL_VALUES:
        assert slug in CONSTRAINT_INTERPRETER_PROMPT, f"Missing {slug!r} in prompt"


def test_prompt_allows_new_slugs():
    assert "novel" in CONSTRAINT_INTERPRETER_PROMPT or "invent" in CONSTRAINT_INTERPRETER_PROMPT


def test_prompt_forbids_null_for_routines():
    assert "null" in CONSTRAINT_INTERPRETER_PROMPT
    assert "recurring" in CONSTRAINT_INTERPRETER_PROMPT or "routine" in CONSTRAINT_INTERPRETER_PROMPT
