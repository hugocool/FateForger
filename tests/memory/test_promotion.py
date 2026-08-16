# tests/memory/test_promotion.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.anchors import AnchorVocabulary
from memory.models import Channel, Observation, Provenance, Tier
from memory.promotion import PromotionReason, decide, is_meta_level

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, channel=Channel.PLANNING, session_id="s0") -> Observation:
    return Observation(
        text=text,
        channel=channel,
        provenance=Provenance.OBSERVED,
        session_id=session_id,
        observed_at=T0,
    )


def _vocab(anchor: str, sessions: int = 8) -> AnchorVocabulary:
    obs = [
        Observation(
            text=f"{anchor} mention",
            channel=Channel.PLANNING,
            provenance=Provenance.OBSERVED,
            session_id=f"s{i}",
            observed_at=T0 + timedelta(days=i),
        )
        for i in range(sessions)
    ]
    return AnchorVocabulary.from_observations(obs, threshold=6)


def test_rule_on_a_durable_anchor_promotes():
    """Oats-before-gym is stated once, but gym is durable."""
    decision = decide(_obs("eat oats two hours before gym"), _vocab("gym"))
    assert decision.tier is Tier.DURABLE
    assert decision.reason is PromotionReason.ANCHOR_RECURRENCE
    assert "gym" in decision.matched_anchors


def test_rule_on_an_ephemeral_anchor_stays_session():
    decision = decide(_obs("hospital visit at 14:00"), _vocab("gym"))
    assert decision.tier is Tier.SESSION
    assert decision.reason is PromotionReason.NONE


def test_review_channel_promotes_regardless_of_anchor():
    """Policy declarations never recur; the channel is the signal."""
    decision = decide(
        _obs(
            "never schedule more than two parallel strategic outcomes",
            channel=Channel.REVIEW,
        ),
        _vocab("gym"),
    )
    assert decision.tier is Tier.DURABLE
    assert decision.reason is PromotionReason.ASSERTION


def test_meta_level_rows_are_rejected_even_on_review_channel():
    decision = decide(
        _obs(
            "the user wants to begin the timeboxing session immediately",
            channel=Channel.REVIEW,
        ),
        _vocab("gym"),
    )
    assert decision.tier is Tier.SESSION
    assert decision.reason is PromotionReason.NONE


def test_is_meta_level_detects_interaction_talk():
    assert is_meta_level("the user wants to begin the timeboxing session immediately")
    assert is_meta_level("the activity must adhere to a timeboxing format")
    assert not is_meta_level("eat oats two hours before gym")


def test_generated_observations_never_promote():
    obs = Observation(
        text="pre-gym oats",
        channel=Channel.CALENDAR,
        provenance=Provenance.GENERATED,
        session_id="s0",
        observed_at=T0,
    )
    decision = decide(obs, _vocab("gym"))
    assert decision.tier is Tier.SESSION
    assert decision.reason is PromotionReason.NONE


def test_a_real_preference_mentioning_a_session_is_not_meta_level():
    """Regression: generic "session" phrasing must not suppress a life rule."""
    assert not is_meta_level("always start the session with a five-minute stretch")
    decision = decide(
        _obs("always start the session with a five-minute stretch at the gym"),
        _vocab("gym"),
    )
    assert decision.tier is Tier.DURABLE
