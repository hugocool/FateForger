from __future__ import annotations

import threading

from autogen_core.memory import MemoryQueryResult

from fateforger.agents.timeboxing.mem0_constraint_memory import (
    Mem0ConstraintMemoryClient,
)


class _FakeMemoryBackend:
    def __init__(self) -> None:
        self.items = []

    async def add(self, content) -> None:
        self.items.append(content)

    async def query(self, query_text: str, **kwargs) -> MemoryQueryResult:
        _ = (query_text, kwargs)
        return MemoryQueryResult(results=list(self.items))


class _StrictThreadVectorClient:
    def __init__(self) -> None:
        self._owner_thread = threading.get_ident()
        self.calls: list[tuple[list[dict], dict, dict, bool]] = []

    def _add_to_vector_store(
        self,
        messages: list[dict],
        metadata: dict,
        filters: dict,
        infer: bool,
    ) -> None:
        assert threading.get_ident() == self._owner_thread
        self.calls.append((messages, metadata, filters, infer))


class _FakeDirectImportBackend:
    def __init__(self, vector_client: _StrictThreadVectorClient) -> None:
        self._client = vector_client
        self.items = []

    async def add(self, content) -> None:
        self.items.append(content)

    async def query(self, query_text: str, **kwargs) -> MemoryQueryResult:
        _ = (query_text, kwargs)
        return MemoryQueryResult(results=list(self.items))


def _record(
    *,
    uid: str,
    name: str,
    rule_kind: str,
    start_date: str,
    end_date: str | None,
    stage: str = "Skeleton",
) -> dict:
    return {
        "constraint_record": {
            "name": name,
            "description": f"{name} description",
            "necessity": "must",
            "status": "locked",
            "source": "user",
            "confidence": 0.9,
            "scope": "profile",
            "applicability": {
                "start_date": start_date,
                "end_date": end_date,
                "days_of_week": ["MO", "TU", "WE", "TH", "FR"],
                "timezone": "Europe/Amsterdam",
                "recurrence": None,
            },
            "lifecycle": {"uid": uid, "supersedes_uids": [], "ttl_days": None},
            "payload": {
                "rule_kind": rule_kind,
                "scalar_params": {"duration_min": 30, "contiguity": "prefer"},
                "windows": [],
            },
            "applies_stages": [stage, "Refine"],
            "applies_event_types": ["DW", "SW"],
            "topics": ["focus"],
        }
    }


async def test_mem0_upsert_and_query_constraints_filters_active_range() -> None:
    backend = _FakeMemoryBackend()
    client = Mem0ConstraintMemoryClient(
        user_id="u1",
        is_cloud=False,
        local_config={"path": ":memory:"},
        memory_backend=backend,
    )

    await client.upsert_constraint(
        record=_record(
            uid="tb_active",
            name="Active constraint",
            rule_kind="capacity",
            start_date="2026-01-01",
            end_date="2026-12-31",
        ),
        event={"action": "upsert"},
    )
    await client.upsert_constraint(
        record=_record(
            uid="tb_expired",
            name="Expired constraint",
            rule_kind="sequencing",
            start_date="2025-01-01",
            end_date="2025-12-31",
        ),
        event={"action": "upsert"},
    )

    rows = await client.query_constraints(
        filters={
            "as_of": "2026-02-13",
            "stage": "Skeleton",
            "event_types_any": ["DW"],
            "statuses_any": ["locked"],
            "scopes_any": ["profile"],
            "necessities_any": ["must"],
            "require_active": True,
        },
        type_ids=None,
        tags=["focus"],
        sort=[["Name", "ascending"]],
        limit=10,
    )

    assert [row["uid"] for row in rows] == ["tb_active"]
    assert rows[0]["rule_kind"] == "capacity"
    assert rows[0]["topics"] == ["focus"]


async def test_mem0_query_types_aggregates_counts() -> None:
    backend = _FakeMemoryBackend()
    client = Mem0ConstraintMemoryClient(
        user_id="u1",
        is_cloud=False,
        local_config={"path": ":memory:"},
        memory_backend=backend,
    )

    await client.upsert_constraint(
        record=_record(
            uid="tb_1",
            name="Type one",
            rule_kind="capacity",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    )
    await client.upsert_constraint(
        record=_record(
            uid="tb_2",
            name="Type two",
            rule_kind="capacity",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    )
    await client.upsert_constraint(
        record=_record(
            uid="tb_3",
            name="Type three",
            rule_kind="sequencing",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    )

    types = await client.query_types(stage="Skeleton", event_types=["DW"])
    assert types
    assert types[0]["type_id"] == "capacity"
    assert types[0]["count"] == 2


async def test_mem0_update_and_archive_constraint() -> None:
    backend = _FakeMemoryBackend()
    client = Mem0ConstraintMemoryClient(
        user_id="u1",
        is_cloud=False,
        local_config={"path": ":memory:"},
        memory_backend=backend,
    )

    await client.upsert_constraint(
        record=_record(
            uid="tb_editable",
            name="Editable constraint",
            rule_kind="capacity",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    )

    updated = await client.update_constraint(
        uid="tb_editable",
        patch={
            "description": "Updated description",
            "topics": ["focus", "planning"],
        },
        event={"action": "manual_update"},
    )
    assert updated["updated"] is True

    loaded = await client.get_constraint(uid="tb_editable")
    assert loaded is not None
    assert loaded["constraint_record"]["description"] == "Updated description"
    assert loaded["constraint_record"]["topics"] == ["focus", "planning"]

    archived = await client.archive_constraint(uid="tb_editable", reason="user request")
    assert archived["updated"] is True

    rows = await client.query_constraints(
        filters={
            "as_of": "2026-02-13",
            "statuses_any": ["declined"],
            "require_active": False,
        },
        type_ids=None,
        tags=None,
        sort=None,
        limit=10,
    )
    assert [row["uid"] for row in rows] == ["tb_editable"]
    assert rows[0]["status"] == "declined"


async def test_mem0_supersede_and_reflection() -> None:
    backend = _FakeMemoryBackend()
    client = Mem0ConstraintMemoryClient(
        user_id="u1",
        is_cloud=False,
        local_config={"path": ":memory:"},
        memory_backend=backend,
    )

    await client.upsert_constraint(
        record=_record(
            uid="tb_old",
            name="Old constraint",
            rule_kind="sequencing",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
    )

    supersede = await client.supersede_constraint(
        uid="tb_old",
        new_record={
            "constraint_record": {
                **_record(
                    uid="tb_new",
                    name="New constraint",
                    rule_kind="sequencing",
                    start_date="2026-01-01",
                    end_date="2026-12-31",
                )["constraint_record"],
                "lifecycle": {"uid": "tb_new", "supersedes_uids": [], "ttl_days": None},
            }
        },
        event={"action": "supersede"},
    )
    assert supersede["updated"] is True
    assert supersede["superseded_uid"] == "tb_old"

    old_rows = await client.query_constraints(
        filters={"statuses_any": ["declined"], "require_active": False},
        type_ids=None,
        tags=None,
        sort=None,
        limit=20,
    )
    assert any(row["uid"] == "tb_old" and row["status"] == "declined" for row in old_rows)

    new_row = await client.get_constraint(uid="tb_new")
    assert new_row is not None
    assert "tb_old" in (
        new_row["constraint_record"].get("lifecycle", {}).get("supersedes_uids") or []
    )

    reflection = await client.add_reflection(
        payload={"summary": "quality improved", "stage": "Refine"}
    )
    assert reflection["saved"] is True
    assert any(
        (item.metadata or {}).get("kind") == "timeboxing_reflection"
        for item in backend.items
    )


async def test_mem0_direct_import_path_stays_on_current_thread() -> None:
    vector_client = _StrictThreadVectorClient()
    backend = _FakeDirectImportBackend(vector_client)
    client = Mem0ConstraintMemoryClient(
        user_id="u1",
        is_cloud=False,
        local_config={"path": ":memory:"},
        memory_backend=backend,
    )

    reflection = await client.add_reflection(
        payload={"summary": "stage refine", "stage": "Refine"}
    )

    assert reflection["saved"] is True
    assert len(vector_client.calls) == 1
    messages, metadata, filters, infer = vector_client.calls[0]
    assert messages[0]["content"] == "stage refine | Refine"
    assert metadata["kind"] == "timeboxing_reflection"
    assert filters["user_id"] == "u1"
    assert infer is False
