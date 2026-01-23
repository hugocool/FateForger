import re

import pytest

from fateforger.tools.constraint_mcp import get_constraint_mcp_tools


@pytest.mark.asyncio
async def test_constraint_mcp_tools_are_openai_safe() -> None:
    """Ensure constraint-memory MCP tools expose OpenAI-compatible names (no dots)."""
    tools = await get_constraint_mcp_tools(timeout=5.0)
    assert tools
    for tool in tools:
        assert "." not in tool.name
        assert re.fullmatch(r"[A-Za-z0-9_-]+", tool.name)

    names = {tool.name for tool in tools}
    assert {
        "constraint_get_store_info",
        "constraint_get_constraint",
        "constraint_query_types",
        "constraint_query_constraints",
        "constraint_upsert_constraint",
        "constraint_log_event",
        "constraint_seed_types",
    }.issubset(names)

