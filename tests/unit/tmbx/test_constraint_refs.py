from __future__ import annotations

from types import SimpleNamespace

from tmbx.journal.constraint_refs import constraint_refs, derived_uid


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


def test_missing_uid_falls_back_to_derived_and_is_tagged():
    refs = constraint_refs([_c()])
    assert refs[0].uid_kind == "derived"
    assert refs[0].uid == derived_uid(_c())


def test_reason_is_carried_through():
    refs = constraint_refs([_c(hints={"uid": "x", "extraction_reason": "graphflow_turn"})])
    assert refs[0].reason == "graphflow_turn"


def test_missing_reason_is_none_not_guessed():
    refs = constraint_refs([_c(hints={"uid": "x"})])
    assert refs[0].reason is None


def test_derived_uid_changes_when_text_is_edited():
    """This is the defect being measured, asserted so it cannot regress silently."""
    before = derived_uid(_c(description="at 18:30"))
    after = derived_uid(_c(description="at 19:00"))
    assert before != after


def test_enum_valued_fields_are_normalised():
    necessity = SimpleNamespace(value="must")
    refs = constraint_refs([_c(necessity=necessity)])
    assert refs[0].uid_kind == "derived"


def test_empty_input():
    assert constraint_refs([]) == []
