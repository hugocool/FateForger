"""/timebox answers through the harness, with the legacy flow one flag away."""

from __future__ import annotations

import os

import pytest

from fateforger.slack_bot.handlers import _timebox_backend, _timebox_body_for_harness


@pytest.fixture(autouse=True)
def _clean_env():
    saved = os.environ.pop("FF_TIMEBOX_BACKEND", None)
    yield
    if saved is not None:
        os.environ["FF_TIMEBOX_BACKEND"] = saved
    else:
        os.environ.pop("FF_TIMEBOX_BACKEND", None)


def test_the_harness_answers_by_default():
    assert _timebox_backend() == "harness"


def test_the_legacy_flow_stays_one_variable_away():
    """A migration nobody can reverse is a rewrite.

    The legacy path is still the only one with the five-stage machine.
    """
    os.environ["FF_TIMEBOX_BACKEND"] = "legacy"
    assert _timebox_backend() == "legacy"


def test_the_flag_is_read_per_call_not_captured_at_import():
    """Flipping it must not require a restart to observe."""
    assert _timebox_backend() == "harness"
    os.environ["FF_TIMEBOX_BACKEND"] = "legacy"
    assert _timebox_backend() == "legacy"
    os.environ["FF_TIMEBOX_BACKEND"] = "harness"
    assert _timebox_backend() == "harness"


def test_an_unrecognised_value_does_not_silently_mean_legacy():
    """Only "legacy" routes away from the harness.

    A typo must not quietly restore the system being migrated off, which is
    the sort of thing nobody notices until the behaviour they were testing
    turns out to be the old one.
    """
    os.environ["FF_TIMEBOX_BACKEND"] = "lgacy"
    assert _timebox_backend() != "legacy"


def test_a_bare_timebox_is_given_the_day_to_plan():
    """The command names the intent; silence is not ambiguity here.

    /dsh refusing an empty message is right — there is nothing to infer. A
    bare /timebox answering "Give me something to plan" reads as the bot not
    knowing what its own command is for.
    """
    text = _timebox_body_for_harness({"text": ""})["text"]
    assert "calendar" in text and "constraints" in text


def test_a_bare_timebox_does_not_commit_anything():
    """It plans on its own initiative, so it must not act on it.

    Nothing gates plan_commit on this path yet (#177), so the instruction is
    the only thing standing between an unprompted plan and the real calendar.
    """
    assert "not commit" in _timebox_body_for_harness({"text": ""})["text"]


def test_what_the_user_typed_is_passed_through_untouched():
    body = {"text": "plan thursday around the client call", "channel_id": "C1"}
    out = _timebox_body_for_harness(body)
    assert out["text"] == body["text"]
    assert out["channel_id"] == "C1"
