from __future__ import annotations

from types import SimpleNamespace

from tmbx.journal.constraint_refs import constraint_refs


def _c(hints=None, name="Dinner", description="at 18:30", necessity="must", scope="profile"):
    return SimpleNamespace(
        hints=hints if hints is not None else {},
        name=name,
        description=description,
        necessity=necessity,
        scope=scope,
    )


def test_minted_uid_is_used_and_tagged():
    refs = constraint_refs([_c(hints={"uid": "abc123"})])
    assert refs[0].uid == "abc123"
    assert refs[0].uid_kind == "minted"


def test_missing_uid_is_unresolvable_not_invented():
    """No hints["uid"] means no identity claim is made — nothing is derived
    from the constraint's text (CLAUDE.md bans content-derived identity)."""
    refs = constraint_refs([_c()])
    assert refs[0].uid_kind == "unresolvable"
    assert refs[0].uid == ""


def test_reason_is_carried_through():
    refs = constraint_refs([_c(hints={"uid": "x", "extraction_reason": "graphflow_turn"})])
    assert refs[0].reason == "graphflow_turn"


def test_missing_reason_is_none_not_guessed():
    refs = constraint_refs([_c(hints={"uid": "x"})])
    assert refs[0].reason is None


def test_empty_input():
    assert constraint_refs([]) == []
