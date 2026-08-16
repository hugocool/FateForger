# tests/memory/test_replay_filter.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.models import Channel, Observation, Provenance
from memory.replay_filter import is_machine_replay, normalize
from memory.store import ObservationStore

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, offset_s: int = 0, session_id: str = "s1") -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id=session_id,
        observed_at=T0 + timedelta(seconds=offset_s),
    )


def test_normalize_collapses_case_and_punctuation_but_keeps_times():
    """Colons survive because times are semantically load-bearing.

    A char class cannot tell the colon in "Window:" from the one in "14:30",
    and preserving both is harmless here: machine replays are byte-identical,
    so an extra colon never prevents a match.
    """
    assert normalize("Work Window: 14:30 to 21:30!") == "work window: 14:30 to 21:30"


def test_identical_text_within_window_is_replay():
    prior = _obs("work window today is 14:30 to 21:30")
    cand = _obs("work window today is 14:30 to 21:30", offset_s=30)
    assert is_machine_replay(cand, [prior]) is True


def test_identical_text_outside_window_is_not_replay():
    prior = _obs("work window today is 14:30 to 21:30")
    cand = _obs("work window today is 14:30 to 21:30", offset_s=3600)
    assert is_machine_replay(cand, [prior]) is False


def test_different_session_is_not_replay():
    prior = _obs("work window today is 14:30 to 21:30", session_id="s1")
    cand = _obs("work window today is 14:30 to 21:30", offset_s=30, session_id="s2")
    assert is_machine_replay(cand, [prior]) is False


def test_different_text_within_window_is_not_replay():
    prior = _obs("work window today is 14:30 to 21:30")
    cand = _obs("daily one thing is facet extraction", offset_s=30)
    assert is_machine_replay(cand, [prior]) is False


def test_append_filtered_suppresses_replay(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    first = store.append_filtered(_obs("wake at 07:00"))
    second = store.append_filtered(_obs("wake at 07:00", offset_s=20))
    assert first is not None
    assert second is None
    assert len(store.all()) == 1
