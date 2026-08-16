# src/memory/replay_filter.py
from __future__ import annotations

import re

from memory.models import Observation

_PUNCT = re.compile(r"[^a-z0-9: ]+")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Times are preserved."""
    lowered = text.lower().strip()
    stripped = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", stripped).strip()


def is_machine_replay(
    candidate: Observation,
    recent: list[Observation],
    window_seconds: int = 60,
) -> bool:
    """True when this observation is the same text re-emitted by machinery.

    The real corpus shows byte-identical user turns repeated within seconds
    (retry loops, resubmits, duplicate logging). Those are not evidence.
    Restatement across sessions, or after the window, is genuine and kept.
    """
    key = normalize(candidate.text)
    for prior in recent:
        if prior.session_id != candidate.session_id:
            continue
        delta = abs((candidate.observed_at - prior.observed_at).total_seconds())
        if delta <= window_seconds and normalize(prior.text) == key:
            return True
    return False
