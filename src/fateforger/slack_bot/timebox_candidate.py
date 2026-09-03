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

    @classmethod
    def from_artifact_payload(cls, payload: object) -> ValidatedTimeboxCandidate:
        """Read the commit basis out of a validated-candidate artifact.

        These four keys are the whole contract between the planner that writes
        them and the two places that read them, and each side spelled it out
        for itself with no type anywhere -- so `digest` could be renamed on one
        side and the other would go on returning "".

        The coercion stays lenient on purpose. An artifact that is missing a
        key has to render as an empty basis and be refused at the commit gate,
        which already checks it; raising here would put the failure inside a
        Slack renderer, where it reaches the user as a turn that went wrong
        rather than as a plan that cannot be committed.
        """

        fields = payload if isinstance(payload, dict) else {}
        snapshot = fields.get("snapshot")
        patch = fields.get("patch")
        return cls(
            digest=str(fields.get("digest") or ""),
            snapshot=snapshot if isinstance(snapshot, dict) else {},
            patch=patch if isinstance(patch, dict) else {},
            rendered=str(fields.get("rendered") or ""),
        )

    def as_commit_basis(self) -> dict[str, Any]:
        """The same four keys, written the way `from_artifact_payload` reads.

        The opaque id and the owner are deliberately absent: they are this
        host's record of who may spend the candidate, not part of what the
        planner produced, and putting them in an artifact payload would make
        them look forgeable by whoever writes the next one.
        """

        return {
            "snapshot": self.snapshot,
            "patch": self.patch,
            "digest": self.digest,
            "rendered": self.rendered,
        }


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
