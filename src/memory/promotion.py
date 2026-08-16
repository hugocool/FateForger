# src/memory/promotion.py
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from memory.anchors import AnchorVocabulary, extract_anchors
from memory.models import Channel, Observation, Provenance, Tier

# Only markers naming the tool itself. Generic phrases like "begin the session"
# were removed: the meta guard short-circuits to SESSION unconditionally, so a
# real preference ("always start the session with a five-minute stretch") would
# be permanently blocked from durable memory — the very failure this task fixes.
# They also caught nothing extra: the polluting row "the user wants to begin the
# timeboxing session immediately" already matches "timeboxing session".
META_MARKERS = (
    "timeboxing session",
    "timeboxing format",
    "timeboxing methodology",
)


class PromotionReason(str, Enum):
    ANCHOR_RECURRENCE = "anchor_recurrence"
    ASSERTION = "assertion"
    NONE = "none"


class PromotionDecision(BaseModel):
    tier: Tier
    reason: PromotionReason
    matched_anchors: list[str] = Field(default_factory=list)


def is_meta_level(text: str) -> bool:
    """True for statements about the interaction rather than about the day.

    The real store contains rows such as "the user wants to begin the
    timeboxing session immediately". These are not preferences about a life.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in META_MARKERS)


def decide(
    observation: Observation, vocabulary: AnchorVocabulary
) -> PromotionDecision:
    """Decide which tier an observation belongs in.

    Two complementary paths. Recurrence promotes a rule when *its anchor* is
    durable, which catches what the user does. Assertion promotes on the
    source channel, which catches what the user decides — policy
    declarations never recur, because declaring something once is how
    declarations work.
    """
    session = PromotionDecision(tier=Tier.SESSION, reason=PromotionReason.NONE)

    if observation.provenance is not Provenance.OBSERVED:
        return session
    if is_meta_level(observation.text):
        return session

    if observation.channel is Channel.REVIEW:
        return PromotionDecision(
            tier=Tier.DURABLE, reason=PromotionReason.ASSERTION
        )

    candidates = extract_anchors(observation.text) | set(observation.anchors)
    matched = sorted(a for a in candidates if vocabulary.is_durable(a))
    if matched:
        return PromotionDecision(
            tier=Tier.DURABLE,
            reason=PromotionReason.ANCHOR_RECURRENCE,
            matched_anchors=matched,
        )
    return session
