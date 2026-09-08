r"""Three call sites collapsed whitespace and none of them did.

All three wrote ``re.sub(r"\\s+", ...)``. In a raw string that is a literal
backslash followed by ``s``, not the whitespace class, so the pattern matched
nothing and every call returned its input unchanged:

    re.sub(r"\\s+", " ", "plan   my\tday") == "plan   my\tday"

A Slack title built from a multi-line message therefore kept its newlines and
tabs, and the truncation that follows counted characters nobody would see.

`re` is also banned outright by this project. Both are fixed by removing it.

The event-id site matters more than it looks: its output is a deterministic
calendar id, and Hugo has a live event under
``ffplanningillv027jsm9md8r8vfbgvepk96al6uoq``. Changing how the input is
normalised would orphan it, so the ids are pinned here first. They are safe
because the broken pattern was a no-op for whitespace-free input, and Slack
user ids have no whitespace.
"""

from fateforger.slack_bot.handlers import (
    _timeboxing_excerpt_from_text,
    _timeboxing_title_from_text,
)
from fateforger.slack_bot.planning_ids import planning_event_id_for_user


def test_a_title_collapses_whitespace() -> None:
    assert _timeboxing_title_from_text("plan   my\tday\nplease") == "plan my day please"


def test_an_excerpt_collapses_whitespace() -> None:
    assert _timeboxing_excerpt_from_text("a\n\nb   c") == "a b c"


def test_a_title_still_truncates_on_visible_length() -> None:
    """The reason this mattered: length was counted over text nobody sees."""

    assert len(_timeboxing_title_from_text("word \n" * 40)) <= 80


def test_the_live_planning_event_ids_do_not_move() -> None:
    """An id change would orphan the event already on Hugo's calendar."""

    assert (
        planning_event_id_for_user("U095637NL8P")
        == "ffplanningillv027jsm9md8r8vfbgvepk96al6uoq"
    )
    assert (
        planning_event_id_for_user("U123")
        == "ffplanningfa5j0v95e0qq6vlqv2nf3raido0ql87p"
    )


def test_the_regex_module_is_gone() -> None:
    import fateforger.slack_bot.handlers as handlers
    import fateforger.slack_bot.planning_ids as planning_ids

    assert not hasattr(handlers, "re")
    assert not hasattr(planning_ids, "re")
