from __future__ import annotations

import json

from autogen_core.memory import MemoryContent, MemoryQueryResult

from fateforger.agents.timeboxing.graphiti_constraint_memory import (
    _GraphitiMcpMemoryBackend,
    GraphitiConstraintMemoryClient,
)


class _FakeWorkbench:
    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, name: str, *, arguments: dict[str, object]) -> object:
        self.calls.append((name, dict(arguments)))
        return self._responses[name]


class _FakeToolResult:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def to_text(self) -> str:
        return json.dumps(self._payload)


async def test_graphiti_mcp_backend_adds_episode_via_mcp() -> None:
    workbench = _FakeWorkbench(
        responses={"add_memory": _FakeToolResult({"message": "ok"})}
    )
    backend = _GraphitiMcpMemoryBackend(
        server_url="http://graphiti-mcp:8000/mcp",
        user_id="U1",
        limit=25,
        workbench=workbench,
    )

    await backend.add(
        MemoryContent(
            content="facet extraction block",
            mime_type="text/plain",
            metadata={"uid": "c-1", "status": "locked"},
        )
    )

    tool_name, arguments = workbench.calls[0]
    assert tool_name == "add_memory"
    assert arguments["group_id"] == "U1"
    assert arguments["source"] == "text"
    assert arguments["episode_body"] == "facet extraction block"
    assert json.loads(str(arguments["source_description"])) == {
        "uid": "c-1",
        "status": "locked",
    }


async def test_graphiti_mcp_backend_queries_recent_episodes_and_ranks_results() -> None:
    workbench = _FakeWorkbench(
        responses={
            "get_episodes": _FakeToolResult(
                [
                    {
                        "uuid": "ep-1",
                        "episode_body": "facet extraction deep work",
                        "source_description": json.dumps({"uid": "c-1"}),
                        "created_at": "2026-03-10T12:00:00+00:00",
                    },
                    {
                        "uuid": "ep-2",
                        "episode_body": "blogging shallow work",
                        "source_description": json.dumps({"uid": "c-2"}),
                        "created_at": "2026-03-10T11:00:00+00:00",
                    },
                ]
            )
        }
    )
    backend = _GraphitiMcpMemoryBackend(
        server_url="http://graphiti-mcp:8000/mcp",
        user_id="U1",
        limit=25,
        workbench=workbench,
    )

    result = await backend.query("facet extraction", limit=5)

    tool_name, arguments = workbench.calls[0]
    assert tool_name == "get_episodes"
    assert arguments == {"group_ids": ["U1"], "max_episodes": 5}
    assert [item.content for item in result.results] == [
        "facet extraction deep work",
        "blogging shallow work",
    ]
    assert result.results[0].metadata["uid"] == "c-1"


class _FakeMemoryBackend:
    def __init__(self) -> None:
        self.items = []

    async def add(self, content) -> None:
        self.items.append(content)

    async def query(self, query_text: str, **kwargs) -> MemoryQueryResult:
        _ = (query_text, kwargs)
        return MemoryQueryResult(results=list(self.items))


def _record(*, uid: str, name: str, rule_kind: str) -> dict:
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
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
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
            "applies_stages": ["Skeleton", "Refine"],
            "applies_event_types": ["DW", "SW"],
            "topics": ["focus"],
        }
    }


def test_graphiti_client_no_longer_subclasses_legacy_backend() -> None:
    assert GraphitiConstraintMemoryClient.__mro__[1].__name__ == "ConstraintRecordMemoryClient"


async def test_graphiti_client_upsert_and_query_constraints_without_legacy_inheritance() -> None:
    backend = _FakeMemoryBackend()
    client = GraphitiConstraintMemoryClient(
        user_id="u1",
        server_url="http://graphiti-mcp:8000/mcp",
        memory_backend=backend,
    )

    await client.upsert_constraint(
        record=_record(uid="tb_active", name="Active constraint", rule_kind="capacity"),
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
        tags=["focus"],
        sort=[["Name", "ascending"]],
        limit=10,
    )

    assert [row["uid"] for row in rows] == ["tb_active"]
    assert rows[0]["rule_kind"] == "capacity"
