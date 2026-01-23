from fateforger.tools import constraint_mcp


def test_sanitize_tool_name_replaces_dots() -> None:
    """Ensure MCP tool names are sanitized for OpenAI tool naming rules."""
    assert constraint_mcp._sanitize_tool_name("constraint.get_store_info") == "constraint_get_store_info"


def test_dedupe_tool_names_appends_suffixes() -> None:
    """Ensure sanitized tool names are de-duplicated deterministically."""
    mapping = constraint_mcp._dedupe_tool_names(
        ["constraint.query_constraints", "constraint.query_constraints"]
    )
    assert mapping["constraint.query_constraints"] == "constraint_query_constraints_1"
