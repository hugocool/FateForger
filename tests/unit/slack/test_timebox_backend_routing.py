"""Which backend /timebox routes to. What the harness is handed lives in
test_timebox_bare_command.py."""

from __future__ import annotations

import os

import pytest

from fateforger.slack_bot.handlers import _timebox_backend


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
