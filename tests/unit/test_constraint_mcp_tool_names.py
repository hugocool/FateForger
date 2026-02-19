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


def test_build_constraint_server_env_injects_notion_defaults(monkeypatch, tmp_path) -> None:
    """MCP subprocess env should inherit Notion creds/page-id from settings when env is empty."""
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("WORK_NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", raising=False)
    monkeypatch.delenv("WORK_NOTION_PARENT_PAGE_ID", raising=False)
    monkeypatch.setattr(constraint_mcp.settings, "work_notion_token", "tok_x")
    monkeypatch.setattr(
        constraint_mcp.settings,
        "notion_timeboxing_parent_page_id",
        "parent_x",
    )

    env = constraint_mcp.build_constraint_server_env(tmp_path)

    assert env["NOTION_TOKEN"] == "tok_x"
    assert env["WORK_NOTION_TOKEN"] == "tok_x"
    assert env["NOTION_TIMEBOXING_PARENT_PAGE_ID"] == "parent_x"
    assert env["WORK_NOTION_PARENT_PAGE_ID"] == "parent_x"


def test_build_constraint_server_env_preserves_explicit_env(monkeypatch, tmp_path) -> None:
    """Explicit env values should win over settings defaults."""
    monkeypatch.setenv("NOTION_TOKEN", "tok_env")
    monkeypatch.setenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "parent_env")
    monkeypatch.setattr(constraint_mcp.settings, "work_notion_token", "tok_settings")
    monkeypatch.setattr(
        constraint_mcp.settings,
        "notion_timeboxing_parent_page_id",
        "parent_settings",
    )

    env = constraint_mcp.build_constraint_server_env(tmp_path)

    assert env["NOTION_TOKEN"] == "tok_env"
    assert env["NOTION_TIMEBOXING_PARENT_PAGE_ID"] == "parent_env"
