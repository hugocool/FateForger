"""Immutable, one-shot ownership for a validated Slack timebox proposal."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class ValidatedTimeboxCandidate:
    """The exact payload tmbx validated and Slack displayed for approval."""

    digest: str
    snapshot: dict[str, Any]
    patch: dict[str, Any]
    rendered: str = ""
    candidate_id: str = ""
    owner_user_id: str = ""

    def with_opaque_id(self) -> ValidatedTimeboxCandidate:
        if self.candidate_id:
            return self
        return replace(self, candidate_id=secrets.token_urlsafe(18))


class PendingTimeboxCandidates:
    """Per-thread proposals; replacement invalidates all older buttons."""

    def __init__(self) -> None:
        self._by_thread: dict[str, ValidatedTimeboxCandidate] = {}

    def replace(
        self,
        thread_key: str,
        candidate: ValidatedTimeboxCandidate,
        *,
        owner_user_id: str,
    ) -> ValidatedTimeboxCandidate:
        owned = replace(
            candidate.with_opaque_id(), owner_user_id=owner_user_id.strip()
        )
        self._by_thread[thread_key] = owned
        return owned

    def consume(
        self,
        thread_key: str,
        candidate_id: str,
        *,
        actor_user_id: str,
    ) -> ValidatedTimeboxCandidate | None:
        candidate = self._by_thread.get(thread_key)
        if candidate is None:
            return None
        if not candidate.owner_user_id or not actor_user_id:
            return None
        if not secrets.compare_digest(candidate.owner_user_id, actor_user_id):
            return None
        if not secrets.compare_digest(candidate.candidate_id, candidate_id):
            return None
        return self._by_thread.pop(thread_key, None)

    def invalidate(self, thread_key: str) -> None:
        self._by_thread.pop(thread_key, None)

    def peek(self, thread_key: str) -> ValidatedTimeboxCandidate | None:
        return self._by_thread.get(thread_key)


__all__ = ["PendingTimeboxCandidates", "ValidatedTimeboxCandidate"]
