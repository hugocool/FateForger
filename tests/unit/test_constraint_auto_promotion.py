"""session_appearances counter increments on merge; auto-promotes MUST at threshold."""
from fateforger.agents.timeboxing.agent import (
    AUTO_PROMOTE_THRESHOLD,
    _increment_session_appearances,
    _should_auto_promote,
)


def test_increment_creates_counter_when_absent():
    result = _increment_session_appearances({})
    assert result["session_appearances"] == 1


def test_increment_adds_to_existing_count():
    result = _increment_session_appearances({"session_appearances": 2})
    assert result["session_appearances"] == 3


def test_increment_does_not_mutate_original():
    lifecycle = {"session_appearances": 1}
    _increment_session_appearances(lifecycle)
    assert lifecycle["session_appearances"] == 1


def test_auto_promote_threshold_is_three():
    assert AUTO_PROMOTE_THRESHOLD == 3


def test_should_auto_promote_at_threshold():
    assert _should_auto_promote(session_appearances=3, necessity="MUST") is True


def test_should_auto_promote_above_threshold():
    assert _should_auto_promote(session_appearances=5, necessity="MUST") is True


def test_should_not_promote_below_threshold():
    assert _should_auto_promote(session_appearances=2, necessity="MUST") is False


def test_should_not_promote_non_must():
    assert _should_auto_promote(session_appearances=5, necessity="SHOULD") is False


def test_should_not_promote_prefer():
    assert _should_auto_promote(session_appearances=10, necessity="PREFER") is False
