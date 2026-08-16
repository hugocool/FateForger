# tests/memory/test_mcp_server.py
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from memory.judge import StubJudge
from memory.mcp_server import build_server
from memory.models import Tier
from memory.service import MemoryService

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _service(tmp_path, judge=None) -> MemoryService:
    return MemoryService(str(tmp_path / "memory.db"), judge or StubJudge())


async def test_the_server_exposes_exactly_the_session_verbs(tmp_path):
    server = build_server(_service(tmp_path))
    tools = {t.name for t in await server.list_tools()}
    assert tools == {"memory_observe", "memory_get_active_constraints"}


async def test_observe_tool_round_trips(tmp_path):
    judge = StubJudge(
        tiers={"no meetings before 13:00": Tier.DURABLE},
        labels={"no meetings before 13:00": "No morning meetings"},
        declarations={"no meetings before 13:00": True},
    )
    server = build_server(_service(tmp_path, judge))
    result = await server.call_tool(
        "memory_observe",
        {"text": "no meetings before 13:00", "session_id": "s1"},
    )
    payload = json.loads(result[0].text)
    assert payload["stored"] is True
    assert payload["constraint_name"] == "No morning meetings"


async def test_read_tool_serves_what_observe_stored(tmp_path):
    judge = StubJudge(
        tiers={"no meetings before 13:00": Tier.DURABLE},
        labels={"no meetings before 13:00": "No morning meetings"},
    )
    server = build_server(_service(tmp_path, judge))
    await server.call_tool(
        "memory_observe",
        {"text": "no meetings before 13:00", "session_id": "s1"},
    )
    result = await server.call_tool(
        "memory_get_active_constraints", {"day": "2026-03-09"}
    )
    # This installed mcp version (1.29.0) only wraps a whole-payload
    # TextContent block around dict-returning tools. A `list[dict]` return
    # type is schemable as a JSON array, so FastMCP additionally emits
    # structured content: call_tool returns (content_blocks, structured)
    # where structured["result"] is exactly the list the tool returned, and
    # content_blocks holds one TextContent *per list item* rather than one
    # block for the whole array. Unwrap accordingly — see task-2-report.md.
    content, structured = result
    rows = structured["result"]
    assert len(rows) == 1
    assert rows[0]["name"] == "No morning meetings"
    assert rows[0]["necessity"] == "should"
    assert "uid" in rows[0]


async def test_the_read_tool_is_not_async_in_the_service(tmp_path):
    """I1 holds through the binding: the read path stays synchronous."""
    import inspect

    from memory.service import MemoryService as S

    assert not inspect.iscoroutinefunction(S.get_active_constraints)
