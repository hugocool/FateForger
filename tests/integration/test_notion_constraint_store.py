import os
from datetime import date

import pytest
import ultimate_notion as uno

pytest.importorskip("ultimate_notion")

from fateforger.adapters.notion.timeboxing_preferences import (
    ConstraintQueryFilters,
    NotionConstraintStore,
    get_notion_session,
    seed_default_constraint_types,
)


def _notion_env_ready() -> bool:
    return bool(
        os.getenv("NOTION_TOKEN")
        and os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID")
    )


@pytest.mark.skipif(
    not _notion_env_ready(), reason="NOTION_TOKEN and NOTION_TIMEBOXING_PARENT_PAGE_ID required"
)
def test_install_is_idempotent():
    session = get_notion_session()
    parent = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "")
    store_a = NotionConstraintStore.from_parent_page(
        parent_page_id=parent, notion=session
    )
    store_b = NotionConstraintStore.from_parent_page(
        parent_page_id=parent, notion=session
    )
    assert store_a.topics_db.id == store_b.topics_db.id
    assert store_a.types_db.id == store_b.types_db.id
    assert store_a.constraints_db.id == store_b.constraints_db.id


@pytest.mark.skipif(
    not _notion_env_ready(), reason="NOTION_TOKEN and NOTION_TIMEBOXING_PARENT_PAGE_ID required"
)
def test_seed_types_and_query():
    session = get_notion_session()
    parent = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "")
    store = NotionConstraintStore.from_parent_page(parent_page_id=parent, notion=session)
    seed_default_constraint_types(store)
    results = store.query_types(stage="Skeleton", event_types=["DW"])
    type_ids = {row["type_id"] for row in results}
    assert "prefer_window" in type_ids
    assert "count_target_per_day" in type_ids


@pytest.mark.skipif(
    not _notion_env_ready(), reason="NOTION_TOKEN and NOTION_TIMEBOXING_PARENT_PAGE_ID required"
)
def test_upsert_constraint_and_supersede():
    session = get_notion_session()
    parent = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "")
    store = NotionConstraintStore.from_parent_page(parent_page_id=parent, notion=session)
    seed_default_constraint_types(store)

    uid_a = "tb:test:dw-cap-1"
    record_a = {
        "constraint_record": {
            "name": "Daily DW count",
            "description": "Two deep work blocks per day.",
            "necessity": "should",
            "status": "locked",
            "source": "user",
            "confidence": 0.9,
            "scope": "profile",
            "type_id": "count_target_per_day",
            "lifecycle": {"uid": uid_a, "supersedes_uids": []},
            "payload": {"rule_kind": "capacity", "scalar_params": {"duration_min": None}},
        }
    }
    page_a = store.upsert_constraint(record_a)
    assert page_a is not None

    uid_b = "tb:test:dw-cap-2"
    record_b = {
        "constraint_record": {
            "name": "Daily DW count update",
            "description": "Three deep work blocks per day.",
            "necessity": "should",
            "status": "locked",
            "source": "user",
            "confidence": 0.9,
            "scope": "profile",
            "type_id": "count_target_per_day",
            "lifecycle": {"uid": uid_b, "supersedes_uids": [uid_a]},
            "payload": {"rule_kind": "capacity", "scalar_params": {"duration_min": None}},
        }
    }
    store.upsert_constraint(record_b)

    superseded = store._get_constraint_by_uid(uid_a)
    assert superseded is not None
    end_date = getattr(superseded.props, "end_date", None)
    assert end_date is not None


@pytest.mark.skipif(
    not _notion_env_ready(), reason="NOTION_TOKEN and NOTION_TIMEBOXING_PARENT_PAGE_ID required"
)
def test_windows_replace():
    session = get_notion_session()
    parent = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "")
    store = NotionConstraintStore.from_parent_page(parent_page_id=parent, notion=session)
    seed_default_constraint_types(store)

    uid = "tb:test:window-1"
    record = {
        "constraint_record": {
            "name": "Afternoon gym",
            "description": "Prefer gym in the afternoon.",
            "necessity": "should",
            "status": "locked",
            "source": "user",
            "confidence": 0.8,
            "scope": "profile",
            "type_id": "prefer_window",
            "lifecycle": {"uid": uid},
            "payload": {
                "rule_kind": "prefer_window",
                "windows": [
                    {"kind": "prefer", "start_time_local": "15:00", "end_time_local": "18:00"}
                ],
            },
        }
    }
    page = store.upsert_constraint(record)
    windows = list(
        store.windows_db.query.filter(
            uno.prop("Constraint").contains(page)
        ).execute()
    )
    assert windows

    record["constraint_record"]["payload"]["windows"] = [
        {"kind": "prefer", "start_time_local": "16:00", "end_time_local": "19:00"}
    ]
    store.upsert_constraint(record)
    windows_after = list(
        store.windows_db.query.filter(
            uno.prop("Constraint").contains(page)
        ).execute()
    )
    assert len(windows_after) == 1
