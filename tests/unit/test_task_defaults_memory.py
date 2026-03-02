from __future__ import annotations

import pytest

from fateforger.agents.tasks.defaults_memory import TaskDefaultsMemoryStore, TaskDueDefaults
from fateforger.core.config import settings


@pytest.fixture(autouse=True)
def _reset_backend_mode_tracking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    TaskDefaultsMemoryStore._reported_fallback_modes.clear()
    TaskDefaultsMemoryStore._reported_durable_modes.clear()
    monkeypatch.setenv(
        "TASKS_DEFAULTS_CACHE_PATH", str(tmp_path / "task_defaults_cache.json")
    )


@pytest.mark.asyncio
async def test_disk_fallback_persists_across_store_instances(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    cache_path = tmp_path / "task_defaults_cache.json"
    monkeypatch.setenv("TASKS_DEFAULTS_CACHE_PATH", str(cache_path))

    store1 = TaskDefaultsMemoryStore()
    monkeypatch.setattr(store1, "_ensure_store", lambda: None)
    defaults = TaskDueDefaults(
        user_id="U1",
        source="ticktick",
        ticktick_project_ids=["P1"],
        ticktick_project_names=["tasks"],
    )
    assert await store1.upsert_user_defaults(defaults) is True

    store2 = TaskDefaultsMemoryStore()
    monkeypatch.setattr(store2, "_ensure_store", lambda: None)
    loaded = await store2.get_user_defaults(user_id="U1")

    assert loaded is not None
    assert loaded.ticktick_project_ids == ["P1"]
    assert loaded.ticktick_project_names == ["tasks"]


def test_backend_selection_defaults_to_task_specific_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "timeboxing_memory_backend", "mem0", raising=False)
    monkeypatch.setattr(
        settings, "tasks_defaults_memory_backend", "constraint_mcp", raising=False
    )
    store = TaskDefaultsMemoryStore()
    assert store._backend == "constraint_mcp"


def test_backend_selection_can_inherit_timeboxing_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "timeboxing_memory_backend", "mem0", raising=False)
    monkeypatch.setattr(
        settings, "tasks_defaults_memory_backend", "inherit_timeboxing", raising=False
    )
    store = TaskDefaultsMemoryStore()
    assert store._backend == "mem0"


@pytest.mark.asyncio
async def test_missing_openai_key_falls_back_with_one_warning_per_user(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "tasks_defaults_memory_backend", "mem0", raising=False)
    monkeypatch.setattr(
        "fateforger.agents.tasks.defaults_memory.build_mem0_client_from_settings",
        lambda user_id: (_ for _ in ()).throw(
            RuntimeError("OPENAI_API_KEY is required for configured Mem0 model")
        ),
    )

    caplog.set_level("WARNING")
    store = TaskDefaultsMemoryStore()

    first = await store.get_user_defaults(user_id="U1")
    second = await store.get_user_defaults(user_id="U1")
    third = await store.get_user_defaults(user_id="U2")

    assert first is None
    assert second is None
    assert third is None
    warning_lines = [
        rec.message
        for rec in caplog.records
        if "backend mode=fallback_cache" in rec.message
    ]
    assert len(warning_lines) == 2
    assert all("reason_code=missing_openai_api_key" in msg for msg in warning_lines)


@pytest.mark.asyncio
async def test_durable_backend_logs_mode_once_and_reads_constraint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        settings, "tasks_defaults_memory_backend", "constraint_mcp", raising=False
    )

    class _Store:
        async def get_constraint(self, *, uid: str):
            assert uid == "taskmarshal-defaults:U1"
            return {
                "constraint_record": {
                    "name": "taskmarshal-defaults:U1",
                    "description": (
                        "task_defaults_json:{"
                        '"user_id":"U1","source":"ticktick","ticktick_project_ids":["P1"],'
                        '"ticktick_project_names":["tasks"],'
                        '"configured_at":"2026-03-02T00:00:00+00:00"}'
                    ),
                }
            }

        async def query_constraints(
            self,
            *,
            filters,
            type_ids=None,
            tags=None,
            sort=None,
            limit=50,
        ):
            _ = filters, type_ids, tags, sort, limit
            return []

    store = TaskDefaultsMemoryStore()
    monkeypatch.setattr(store, "_ensure_store", lambda: _Store())
    caplog.set_level("INFO")

    loaded_first = await store.get_user_defaults(user_id="U1")
    loaded_second = await store.get_user_defaults(user_id="U1")

    assert loaded_first is not None
    assert loaded_second is not None
    assert loaded_first.ticktick_project_ids == ["P1"]
    durable_lines = [
        rec.message for rec in caplog.records if "backend mode=durable" in rec.message
    ]
    assert len(durable_lines) == 1


@pytest.mark.asyncio
async def test_runtime_durable_lookup_failure_downgrades_to_fallback_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        settings, "tasks_defaults_memory_backend", "constraint_mcp", raising=False
    )

    class _FailingStore:
        async def get_constraint(self, *, uid: str):
            _ = uid
            raise RuntimeError("NOTION_TIMEBOXING_PARENT_PAGE_ID is not accessible")

        async def query_constraints(
            self,
            *,
            filters,
            type_ids=None,
            tags=None,
            sort=None,
            limit=50,
        ):
            _ = filters, type_ids, tags, sort, limit
            raise RuntimeError("NOTION_TIMEBOXING_PARENT_PAGE_ID is not accessible")

    store = TaskDefaultsMemoryStore()
    store._store = _FailingStore()
    caplog.set_level("WARNING")

    first = await store.get_user_defaults(user_id="U1")
    second = await store.get_user_defaults(user_id="U1")

    assert first is None
    assert second is None
    fallback_lines = [
        rec.message
        for rec in caplog.records
        if "backend mode=fallback_cache" in rec.message
    ]
    assert len(fallback_lines) == 1
