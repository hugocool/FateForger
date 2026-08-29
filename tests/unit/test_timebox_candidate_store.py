from __future__ import annotations

from fateforger.slack_bot.timebox_candidate import (
    PendingTimeboxCandidates,
    ValidatedTimeboxCandidate,
)


def _candidate(candidate_id: str, day: str) -> ValidatedTimeboxCandidate:
    return ValidatedTimeboxCandidate(
        candidate_id=candidate_id,
        digest="a" * 64,
        snapshot={"day": day, "calendar_id": "primary"},
        patch={"ops": []},
    )


def test_duplicate_approval_delivery_consumes_candidate_only_once():
    store = PendingTimeboxCandidates()
    candidate = store.replace(
        "C1:1", _candidate("candidate-A", "2026-08-29"), owner_user_id="U1"
    )

    assert store.consume("C1:1", "candidate-A", actor_user_id="U1") == candidate
    assert store.consume("C1:1", "candidate-A", actor_user_id="U1") is None


def test_stale_approval_cannot_consume_a_newer_displayed_candidate():
    store = PendingTimeboxCandidates()
    store.replace(
        "C1:1", _candidate("candidate-A", "2026-08-29"), owner_user_id="U1"
    )
    newest = store.replace(
        "C1:1", _candidate("candidate-B", "2026-08-30"), owner_user_id="U1"
    )

    assert store.consume("C1:1", "candidate-A", actor_user_id="U1") is None
    assert store.consume("C1:1", "candidate-B", actor_user_id="U1") == newest


def test_material_new_turn_invalidates_the_displayed_candidate():
    store = PendingTimeboxCandidates()
    store.replace(
        "C1:1", _candidate("candidate-A", "2026-08-29"), owner_user_id="U1"
    )

    store.invalidate("C1:1")

    assert store.consume("C1:1", "candidate-A", actor_user_id="U1") is None


def test_unauthorized_actor_cannot_consume_another_users_candidate():
    """Catches removing the owner comparison from the approval spend path."""

    store = PendingTimeboxCandidates()
    candidate = store.replace(
        "C1:1", _candidate("candidate-A", "2026-08-29"), owner_user_id="U1"
    )

    assert (
        store.consume("C1:1", candidate.candidate_id, actor_user_id="U2") is None
    )
    assert (
        store.consume("C1:1", candidate.candidate_id, actor_user_id="U1")
        == candidate
    )
