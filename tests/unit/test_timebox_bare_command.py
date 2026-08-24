"""A bare `/timebox` has to choose a day, and it used to choose wrongly.

Until 2026-08-24 it asked for "today's calendar". At 20:00 there is no day left
to plan, so every evening the command answered the wrong question — and it did
so silently, because a plan for the remaining four hours of a Sunday looks like
a plan.
"""

from __future__ import annotations

from fateforger.slack_bot.handlers import _timebox_body_for_harness


def test_a_typed_request_is_left_exactly_alone():
    """The command names the intent; the words are still his."""
    body = _timebox_body_for_harness({"text": "plan thursday around the F1 race"})
    assert body["text"] == "plan thursday around the F1 race"


def test_whitespace_only_counts_as_bare():
    assert _timebox_body_for_harness({"text": "   "})["text"] != "   "


def test_a_bare_command_does_not_hardcode_a_day():
    """The defect this replaces.

    A cutoff hour in Python would swap one wrong answer for another — 18:00 is
    late for someone who plans over dinner and early for someone who works past
    it. The clock is read by the model, which already reads it.
    """
    text = _timebox_body_for_harness({"text": ""})["text"]
    assert "today's calendar" not in text
    assert "current date and time" in text


def test_a_bare_command_makes_the_day_correctable_in_one_reply():
    """An assumption he can see is one he can correct.

    Without this the day is chosen invisibly and the only way to notice is to
    read the plan closely enough to spot the date — which is exactly the
    unmarked-assumption failure the standing instructions exist to prevent.
    """
    text = _timebox_body_for_harness({"text": ""})["text"]
    # Naming the day is not enough on its own: buried in paragraph three it is
    # as invisible as not saying it. The correction has to be cheap, which
    # means up front and explicitly invited.
    assert "which day you chose" in text
    assert "first line" in text
    assert "correct it in one reply" in text


def test_a_bare_command_still_refuses_to_commit_unasked():
    """The default must not smuggle in permission the user never gave."""
    assert "not commit" in _timebox_body_for_harness({"text": ""})["text"]


def test_the_body_is_otherwise_untouched():
    body = _timebox_body_for_harness({"text": "", "channel_id": "C1", "user_id": "U1"})
    assert body["channel_id"] == "C1"
    assert body["user_id"] == "U1"
