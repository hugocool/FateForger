from __future__ import annotations

import jsonpatch

from fateforger.agents.timeboxing.durable_constraint_store import (
    ClientBackedDurableConstraintStore,
)


class _ClientWithCoreMethods:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {
            "uid1": {"uid": "uid1", "name": "One", "status": "locked"}
        }
        self.reflections: list[dict] = []

    async def get_store_info(self) -> dict:
        return {"backend": "fake"}

    async def query_types(self, *, stage=None, event_types=None) -> list[dict]:
        _ = (stage, event_types)
        return [{"type_id": "capacity", "count": 1}]

    async def query_constraints(self, *, filters, type_ids=None, tags=None, sort=None, limit=50):
        _ = (filters, type_ids, tags, sort, limit)
        return list(self.rows.values())

    async def upsert_constraint(self, *, record, event=None) -> dict:
        _ = event
        constraint = dict(record.get("constraint_record") or {})
        lifecycle = dict(constraint.get("lifecycle") or {})
        uid = str(lifecycle.get("uid") or constraint.get("uid") or "uid-created")
        self.rows[uid] = {"uid": uid, "status": constraint.get("status", "locked")}
        return {"uid": uid}

    async def update_constraint(self, *, uid, patch, event=None) -> dict:
        _ = (event,)
        row = self.rows.get(uid)
        if not row:
            return {"uid": uid, "updated": False}
        row.update(dict(patch or {}))
        return {"uid": uid, "updated": True}

    async def add_reflection(self, *, payload) -> dict:
        self.reflections.append(dict(payload))
        return {"saved": True}


class _EquivalenceClient:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {
            "uid_existing": {
                "uid": "uid_existing",
                "name": "No calls after 17:00",
                "status": "locked",
                "necessity": "must",
                "constraint_record": {
                    "name": "No calls after 17:00",
                    "description": "Avoid meetings after 17:00.",
                    "necessity": "must",
                    "status": "locked",
                    "scope": "profile",
                    "topics": ["meetings"],
                    "applies_stages": ["CollectConstraints"],
                    "applies_event_types": ["M"],
                    "applicability": {
                        "days_of_week": ["MO", "TU"],
                        "timezone": "Europe/Amsterdam",
                    },
                    "payload": {
                        "rule_kind": "avoid_window",
                        "scalar_params": {"duration_min": 30},
                        "windows": [
                            {
                                "kind": "avoid",
                                "start_time_local": "17:00",
                                "end_time_local": "23:59",
                            }
                        ],
                    },
                    "lifecycle": {"uid": "uid_existing"},
                },
                "updated_at": "2026-02-27T12:00:00+00:00",
            },
            "uid_duplicate": {
                "uid": "uid_duplicate",
                "name": "No calls after 17:00",
                "status": "proposed",
                "necessity": "should",
                "constraint_record": {
                    "name": "No calls after 17:00",
                    "description": "Keep afternoons clear.",
                    "necessity": "should",
                    "status": "proposed",
                    "scope": "profile",
                    "topics": ["meetings"],
                    "applies_stages": ["CollectConstraints"],
                    "applies_event_types": ["M"],
                    "applicability": {
                        "days_of_week": ["TU", "MO"],
                        "timezone": "Europe/Amsterdam",
                    },
                    "payload": {
                        "rule_kind": "avoid_window",
                        "scalar_params": {"duration_min": 30},
                        "windows": [
                            {
                                "kind": "avoid",
                                "start_time_local": "17:00",
                                "end_time_local": "23:59",
                            }
                        ],
                    },
                    "lifecycle": {"uid": "uid_duplicate"},
                },
                "updated_at": "2026-02-27T11:00:00+00:00",
            },
        }
        self.archived: list[str] = []
        self.updated: list[tuple[str, dict]] = []

    async def get_store_info(self) -> dict:
        return {"backend": "fake"}

    async def query_types(self, *, stage=None, event_types=None) -> list[dict]:
        _ = (stage, event_types)
        return []

    async def query_constraints(self, *, filters, type_ids=None, tags=None, sort=None, limit=50):
        _ = (filters, type_ids, tags, sort, limit)
        return [{"uid": uid, **item} for uid, item in self.items.items()]

    async def upsert_constraint(self, *, record, event=None) -> dict:
        _ = (record, event)
        return {"uid": "uid_created"}

    async def get_constraint(self, *, uid) -> dict | None:
        item = self.items.get(uid)
        if item is None:
            return None
        return {
            "uid": uid,
            "constraint_record": dict(item["constraint_record"]),
            "metadata": {"uid": uid, "updated_at": item.get("updated_at")},
        }

    async def update_constraint(self, *, uid, patch, event=None) -> dict:
        _ = event
        self.updated.append((uid, dict(patch or {})))
        if uid not in self.items:
            return {"uid": uid, "updated": False}
        if isinstance(patch, dict):
            self.items[uid]["constraint_record"] = dict(
                patch.get("constraint_record") or self.items[uid]["constraint_record"]
            )
        return {"uid": uid, "updated": True}

    async def archive_constraint(self, *, uid, reason=None) -> dict:
        _ = reason
        if uid not in self.items:
            return {"uid": uid, "updated": False}
        self.archived.append(uid)
        self.items[uid]["constraint_record"]["status"] = "declined"
        return {"uid": uid, "updated": True}


async def test_store_adapter_falls_back_get_by_query_when_backend_has_no_get() -> None:
    store = ClientBackedDurableConstraintStore(client=_ClientWithCoreMethods())
    got = await store.get_constraint(uid="uid1")
    assert got is not None
    assert got["uid"] == "uid1"


async def test_store_adapter_supersede_archives_previous_uid() -> None:
    base = _ClientWithCoreMethods()
    store = ClientBackedDurableConstraintStore(client=base)
    result = await store.supersede_constraint(
        uid="uid1",
        new_record={"constraint_record": {"lifecycle": {"uid": "uid2"}}},
        event={"action": "supersede"},
    )
    assert result["updated"] is True
    assert base.rows["uid1"]["status"] == "declined"
    assert "uid2" in base.rows


async def test_store_adapter_add_reflection_delegates_when_supported() -> None:
    base = _ClientWithCoreMethods()
    store = ClientBackedDurableConstraintStore(client=base)
    result = await store.add_reflection(payload={"summary": "ok"})
    assert result["saved"] is True
    assert base.reflections == [{"summary": "ok"}]


async def test_store_adapter_finds_equivalent_constraint_from_semantic_identity() -> None:
    client = _EquivalenceClient()
    store = ClientBackedDurableConstraintStore(client=client)
    incoming = {
        "constraint_record": {
            "name": "No calls after 17:00",
            "description": "Please avoid meetings after five.",
            "necessity": "should",
            "status": "proposed",
            "scope": "profile",
            "topics": ["meetings"],
            "applies_stages": ["CollectConstraints"],
            "applies_event_types": ["M"],
            "applicability": {"days_of_week": ["MO", "TU"], "timezone": "Europe/Amsterdam"},
            "payload": {
                "rule_kind": "avoid_window",
                "scalar_params": {"duration_min": 30},
                "windows": [
                    {
                        "kind": "avoid",
                        "start_time_local": "17:00",
                        "end_time_local": "23:59",
                    }
                ],
            },
        }
    }
    match = await store.find_equivalent_constraint(record=incoming)
    assert match is not None
    assert match["uid"] == "uid_existing"


async def test_store_adapter_dedupe_archives_non_canonical_duplicates() -> None:
    client = _EquivalenceClient()
    store = ClientBackedDurableConstraintStore(client=client)
    result = await store.dedupe_constraints(limit=50, dry_run=False)
    assert result["duplicates_found"] == 1
    assert result["duplicates_archived"] == 1
    assert client.archived == ["uid_duplicate"]
    assert client.updated, "canonical should be updated with supersedes list"


def test_build_constraint_json_patch_ops_uses_framework_patch() -> None:
    current = {
        "name": "No calls after 17:00",
        "description": "Avoid meetings after 17:00.",
        "payload": {"rule_kind": "avoid_window"},
    }
    merged = {
        "name": "No calls after 17:00",
        "description": "Avoid meetings after 17:30.",
        "payload": {"rule_kind": "avoid_window"},
        "topics": ["meetings"],
    }
    ops = ClientBackedDurableConstraintStore.build_constraint_json_patch_ops(
        current=current,
        merged=merged,
    )
    assert ops
    assert all(str(op.get("path", "")).startswith("/constraint_record") for op in ops)
    patched = jsonpatch.apply_patch({"constraint_record": current}, ops, in_place=False)
    assert patched["constraint_record"] == merged
