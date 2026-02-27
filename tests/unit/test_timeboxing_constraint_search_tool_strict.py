from __future__ import annotations

from autogen_core import CancellationToken

from fateforger.agents.timeboxing import agent as timeboxing_agent_mod


def test_constraint_search_tool_is_strict() -> None:
    """Ensure stage-gating search tool uses strict function-tool schema."""
    agent = timeboxing_agent_mod.TimeboxingFlowAgent.__new__(
        timeboxing_agent_mod.TimeboxingFlowAgent
    )
    agent._constraint_memory_client = None
    tool = timeboxing_agent_mod.TimeboxingFlowAgent._build_constraint_search_tool(agent)

    assert tool.schema.get("strict") is True
    params = tool.schema.get("parameters", {})
    required = set(params.get("required", []))
    assert {"queries", "planned_date", "stage"} == required
    query_items = (
        params.get("properties", {})
        .get("queries", {})
        .get("items", {})
    )
    assert query_items.get("type") == "object"
    assert query_items.get("additionalProperties") is False
    item_required = set(query_items.get("required", []))
    assert {
        "label",
        "text_query",
        "event_types",
        "tags",
        "statuses",
        "scopes",
        "necessities",
        "limit",
    }.issubset(item_required)


async def test_constraint_search_tool_skips_empty_stage1_query(monkeypatch) -> None:
    """Stage 1 no-op query facets should not hit Notion search path."""
    called = {"search": 0}

    async def _fake_search_constraints(*_args, **_kwargs):
        called["search"] += 1
        return "should_not_be_called"

    monkeypatch.setattr(
        timeboxing_agent_mod,
        "search_constraints",
        _fake_search_constraints,
    )

    agent = timeboxing_agent_mod.TimeboxingFlowAgent.__new__(
        timeboxing_agent_mod.TimeboxingFlowAgent
    )
    agent._constraint_memory_client = None
    tool = timeboxing_agent_mod.TimeboxingFlowAgent._build_constraint_search_tool(agent)
    out = await tool.run_json(
        {
            # OpenAI strict schemas require all query keys to be present.
            "queries": [
                {
                    "label": "empty",
                    "text_query": None,
                    "event_types": None,
                    "tags": None,
                    "statuses": None,
                    "scopes": None,
                    "necessities": None,
                    "limit": 20,
                }
            ],
            "planned_date": "2026-02-18",
            "stage": "CollectConstraints",
        },
        CancellationToken(),
    )

    assert called["search"] == 0
    assert "Skipped search_constraints for Stage 1" in str(out)
