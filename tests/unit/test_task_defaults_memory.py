from __future__ import annotations

import pytest

import fateforger.agents.tasks.defaults_memory as defaults_memory_mod
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


def test_defaults_store_uses_graphiti_backend_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    store = TaskDefaultsMemoryStore()
    sentinel_client = object()
    sentinel_store = object()
    captured: dict[str, str] = {}

    def _fake_build_graphiti(*, user_id: str):
        captured["user_id"] = user_id
        return sentinel_client

    def _fake_build_store(client):
        assert client is sentinel_client
        return sentinel_store

    monkeypatch.setattr(
        defaults_memory_mod.settings, "timeboxing_memory_backend", "graphiti", raising=False
    )
    monkeypatch.setattr(
        defaults_memory_mod.settings, "graphiti_user_id", "user-graphiti", raising=False
    )
    monkeypatch.setattr(
        defaults_memory_mod, "build_graphiti_client_from_settings", _fake_build_graphiti
    )
    monkeypatch.setattr(defaults_memory_mod, "build_durable_constraint_store", _fake_build_store)

    resolved = store._ensure_store()

    assert resolved is sentinel_store
    assert captured["user_id"] == "user-graphiti"
