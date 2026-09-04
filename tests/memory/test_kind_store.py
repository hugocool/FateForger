# tests/memory/test_kind_store.py
"""The registry of enforceable kinds (#212, spec §1).

A kind is a minted identity: written only by a promotion, compared by equality
ever after. The store validates the slug's *shape* -- an identifier this system
owns -- and never its meaning.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.kind_store import (
    DuplicateKind,
    EnforceableKind,
    EnforceableKindStore,
    validate_slug,
)

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)


def _kind(slug: str = "planning") -> EnforceableKind:
    return EnforceableKind(
        slug=slug, anchor_uid="a" * 32, rule_observation_uid="o" * 32, created_at=T0
    )


def test_a_kind_is_added_read_back_and_listed(tmp_path):
    store = EnforceableKindStore(str(tmp_path / "m.db"))
    store.add(_kind("planning"))
    store.add(_kind("sleep"))
    assert store.get("planning") == _kind("planning")
    assert store.get("missing") is None
    assert store.slugs() == ["planning", "sleep"]
    assert [k.slug for k in store.all()] == ["planning", "sleep"]


def test_a_duplicate_slug_is_refused_not_overwritten(tmp_path):
    store = EnforceableKindStore(str(tmp_path / "m.db"))
    store.add(_kind("planning"))
    with pytest.raises(DuplicateKind):
        store.add(_kind("planning"))
    assert store.slugs() == ["planning"]


@pytest.mark.parametrize("bad", ["", "Planning", "plan ning", "-planning", "planning-", "plan_ning", "plän"])
def test_a_slug_is_a_lowercase_hyphenated_identifier(bad):
    with pytest.raises(ValueError):
        validate_slug(bad)


def test_valid_slugs_pass_unchanged():
    assert validate_slug("planning") == "planning"
    assert validate_slug("morning-routine") == "morning-routine"


def test_the_model_validates_its_slug():
    with pytest.raises(ValueError):
        _kind("Not A Slug")
