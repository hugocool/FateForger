from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from memory.models import Observation, Provenance

STOPWORDS = frozenset(
    """
    the a an of to for is are be user and in at on with we do dont want need
    have has can will would should could not no yes ok so but if then just
    also very really much more most some any all this that these those from
    into one two use used usually their its only block blocks time must
    always never each every day daily set preference constraint duration
    schedule scheduling
    """.split()
)

_TOKEN = re.compile(r"[a-z][a-z0-9]{2,}")


def extract_anchors(text: str) -> set[str]:
    """Content words that could name a recurring kind of thing."""
    return {t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS}


@dataclass
class AnchorVocabulary:
    """Anchors scored by how many distinct sessions mention them.

    Recurrence counts sessions, never rows: ten mentions inside one
    conversation is one session's worth of evidence, and the real corpus is
    roughly half machine duplication.
    """

    threshold: int = 6
    _sessions: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    @classmethod
    def from_observations(
        cls, observations: list[Observation], threshold: int = 6
    ) -> AnchorVocabulary:
        vocab = cls(threshold=threshold)
        for obs in observations:
            if obs.provenance is not Provenance.OBSERVED:
                continue
            session = obs.session_id or obs.uid
            for anchor in extract_anchors(obs.text) | set(obs.anchors):
                vocab._sessions[anchor].add(session)
        return vocab

    def recurrence(self, anchor: str) -> int:
        return len(self._sessions.get(anchor, ()))

    def is_durable(self, anchor: str) -> bool:
        return self.recurrence(anchor) >= self.threshold

    def durable(self) -> set[str]:
        return {a for a in self._sessions if self.is_durable(a)}
