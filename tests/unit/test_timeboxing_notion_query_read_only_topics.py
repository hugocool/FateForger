from __future__ import annotations

from datetime import date

import fateforger.adapters.notion.timeboxing_preferences as prefs_mod
from fateforger.adapters.notion.timeboxing_preferences import (
    ConstraintQueryFilters,
    NotionConstraintStore,
)


class _DummyQuery:
    def filter(self, _condition):
        return self

    def execute(self):
        return []


class _DummyDB:
    query = _DummyQuery()


class _NoopCondition:
    def __and__(self, _other):
        return self


def test_resolve_topics_skips_creation_when_create_missing_false(monkeypatch):
    store = NotionConstraintStore.__new__(NotionConstraintStore)
    store.topics_db = _DummyDB()

    created: list[str] = []

    def _create(*, name: str, description: str):
        _ = description
        created.append(name)
        return {"name": name}

    monkeypatch.setattr(prefs_mod.TBTopic, "create", staticmethod(_create))

    out = store._resolve_topics_by_name(["unknown-topic"], create_missing=False)

    assert out == []
    assert created == []


def test_resolve_topics_creates_when_enabled(monkeypatch):
    store = NotionConstraintStore.__new__(NotionConstraintStore)
    store.topics_db = _DummyDB()

    created: list[str] = []

    def _create(*, name: str, description: str):
        _ = description
        created.append(name)
        return {"name": name}

    monkeypatch.setattr(prefs_mod.TBTopic, "create", staticmethod(_create))

    out = store._resolve_topics_by_name(["new-topic"], create_missing=True)

    assert len(out) == 1
    assert created == ["new-topic"]


def test_query_constraints_uses_read_only_topic_resolution(monkeypatch):
    store = NotionConstraintStore.__new__(NotionConstraintStore)
    store.constraints_db = _DummyDB()

    captured_create_missing: list[bool] = []

    def _resolve_topics(_names, *, create_missing=True, memo=None):
        _ = memo
        captured_create_missing.append(create_missing)
        return []

    monkeypatch.setattr(store, "_resolve_topics_by_name", _resolve_topics)
    monkeypatch.setattr(store, "_filters_to_condition", lambda _filters: _NoopCondition())

    out = store.query_constraints(
        filters=ConstraintQueryFilters(as_of=date(2026, 2, 18)),
        tags=["missing-topic"],
    )

    assert out == []
    assert captured_create_missing == [False]
