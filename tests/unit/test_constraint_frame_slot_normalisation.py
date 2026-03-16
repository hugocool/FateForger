"""frame_slot aliases must be normalised to canonical slugs on load."""
from fateforger.agents.timeboxing.agent import _normalise_frame_slot


def test_evening_ritual_maps_to_evening_wind_down():
    assert _normalise_frame_slot("evening_ritual") == "evening_wind_down"


def test_pre_sleep_prep_maps_to_shutdown():
    assert _normalise_frame_slot("pre_sleep_prep") == "shutdown"


def test_canonical_value_unchanged():
    assert _normalise_frame_slot("morning_ritual") == "morning_ritual"
    assert _normalise_frame_slot("sleep_target") == "sleep_target"
    assert _normalise_frame_slot("dinner") == "dinner"


def test_none_returns_none():
    assert _normalise_frame_slot(None) is None


def test_empty_returns_none():
    assert _normalise_frame_slot("") is None


def test_unknown_slug_returned_as_is():
    assert _normalise_frame_slot("saxophone_practice") == "saxophone_practice"
