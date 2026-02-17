from types import SimpleNamespace

from fateforger.adapters.notion.timeboxing_preferences import NotionConstraintStore


def _build_store_with_schema(**attrs):
    store = NotionConstraintStore.__new__(NotionConstraintStore)
    store.constraints_db = SimpleNamespace(schema=SimpleNamespace(**attrs))
    return store


def test_constraint_attr_alias_prefers_present_candidate():
    store = _build_store_with_schema(duration_min_min=object())

    assert (
        store._constraint_attr_alias("duration_min", "duration_min_min")
        == "duration_min_min"
    )


def test_filter_constraint_schema_props_drops_unknown_keys():
    store = _build_store_with_schema(name=object(), duration_min_min=object())

    filtered = store._filter_constraint_schema_props(
        {
            "name": "x",
            "duration_min": 60,
            "duration_min_min": 60,
            "unknown_field": "y",
        }
    )

    assert filtered == {"name": "x", "duration_min_min": 60}
