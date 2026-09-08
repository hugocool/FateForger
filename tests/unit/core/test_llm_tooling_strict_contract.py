from __future__ import annotations

import pytest

from fateforger.llm.tooling import assert_strict_tools_for_structured_output


class _Tool:
    def __init__(self, *, name: str, strict: bool) -> None:
        self.name = name
        self.schema = {"name": name, "strict": strict}


def test_assert_strict_tools_noop_without_output_type() -> None:
    assert_strict_tools_for_structured_output(
        tools=[_Tool(name="a", strict=False)],
        output_content_type=None,
        agent_name="TestAgent",
    )


def test_assert_strict_tools_raises_for_non_strict_tools() -> None:
    with pytest.raises(RuntimeError) as exc:
        assert_strict_tools_for_structured_output(
            tools=[_Tool(name="search_constraints", strict=False)],
            output_content_type=dict,
            agent_name="StageCollectConstraints",
        )
    assert "search_constraints" in str(exc.value)


def test_assert_strict_tools_accepts_all_strict() -> None:
    assert_strict_tools_for_structured_output(
        tools=[_Tool(name="search_constraints", strict=True)],
        output_content_type=dict,
        agent_name="StageCollectConstraints",
    )
