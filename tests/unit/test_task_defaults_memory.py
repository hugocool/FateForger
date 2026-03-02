from __future__ import annotations

import pytest

from fateforger.agents.tasks.defaults_memory import TaskDefaultsMemoryStore, TaskDueDefaults


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
