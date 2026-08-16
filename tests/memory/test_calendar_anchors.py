from __future__ import annotations

from datetime import datetime, timezone

from memory.anchors import AnchorVocabulary
from memory.calendar_anchors import CalendarEvent, observations_from_events
from memory.models import Channel, Provenance


def _ev(title: str, day: int) -> CalendarEvent:
    return CalendarEvent(
        title=title, start=datetime(2026, 3, day, 18, 0, tzinfo=timezone.utc)
    )


def test_event_becomes_a_calendar_channel_observation():
    obs = observations_from_events([_ev("Gym", 9)])
    assert len(obs) == 1
    assert obs[0].channel is Channel.CALENDAR
    assert obs[0].provenance is Provenance.OBSERVED
    assert "gym" in obs[0].anchors


def test_one_event_can_carry_multiple_anchors():
    """Real calendar contains a block literally titled 'hockey/running'."""
    obs = observations_from_events([_ev("hockey/running", 17)])
    assert {"hockey", "running"} <= set(obs[0].anchors)


def test_each_day_is_a_distinct_session_for_recurrence():
    events = [_ev("Gym", d) for d in range(2, 10)]
    vocab = AnchorVocabulary.from_observations(
        observations_from_events(events), threshold=6
    )
    assert vocab.recurrence("gym") == 8
    assert vocab.is_durable("gym") is True


def test_calendar_recovers_gym_which_text_alone_misses():
    """The measured failure: gym scores 0 from constraint text alone."""
    vocab_text_only = AnchorVocabulary.from_observations([], threshold=6)
    assert vocab_text_only.is_durable("gym") is False

    events = [_ev("Gym", d) for d in range(2, 10)]
    vocab_with_calendar = AnchorVocabulary.from_observations(
        observations_from_events(events), threshold=6
    )
    assert vocab_with_calendar.is_durable("gym") is True
