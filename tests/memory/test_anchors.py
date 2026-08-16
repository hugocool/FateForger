from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.anchors import AnchorVocabulary, extract_anchors
from memory.models import Channel, Observation, Provenance

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, session_id: str, provenance=Provenance.OBSERVED) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=provenance,
        session_id=session_id,
        observed_at=T0 + timedelta(days=int(session_id[1:])),
    )


def test_extract_drops_stopwords_and_short_tokens():
    got = extract_anchors("The user must wake up at 07:00 for the commute")
    assert "wake" in got
    assert "commute" in got
    assert "the" not in got
    assert "up" not in got


def test_recurrence_counts_distinct_sessions_not_rows():
    """Ten rows in one session is one session's worth of evidence."""
    obs = [_obs("wake at 07:00", "s1") for _ in range(10)]
    vocab = AnchorVocabulary.from_observations(obs, threshold=2)
    assert vocab.recurrence("wake") == 1
    assert vocab.is_durable("wake") is False


def test_anchor_becomes_durable_at_threshold():
    obs = [_obs("wake at 07:00", f"s{i}") for i in range(6)]
    vocab = AnchorVocabulary.from_observations(obs, threshold=6)
    assert vocab.recurrence("wake") == 6
    assert vocab.is_durable("wake") is True


def test_generated_observations_are_excluded():
    """A rule's own output must not make its anchor look durable."""
    obs = [
        _obs("pre-gym oats", f"s{i}", provenance=Provenance.GENERATED)
        for i in range(10)
    ]
    vocab = AnchorVocabulary.from_observations(obs, threshold=2)
    assert vocab.recurrence("oats") == 0
    assert vocab.is_durable("oats") is False


def test_durable_returns_the_vocabulary():
    obs = [_obs("wake at 07:00", f"s{i}") for i in range(6)]
    obs += [_obs("hospital visit", f"h{i}") for i in range(2)]
    vocab = AnchorVocabulary.from_observations(obs, threshold=6)
    assert vocab.durable() == {"wake"}
