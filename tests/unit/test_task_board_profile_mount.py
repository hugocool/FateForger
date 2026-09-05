"""The task_board mount exists, names the right module, and stays off by default.

The harness MCP client has no tool allow-list, and `tools.restrict()` refuses a
global scope, so every tool of every mounted server lands in the root planner's
prompt. The only lever left is the mount's own `disabled` flag, which is why the
row is gated on FF_TASK_TOOLS rather than trusted to be ignored. A row that
loses that gate costs two tool schemas on every planning call, silently, so it
is pinned here.

Like `test_dsh_profile_env_defaults.py`, this reads the versioned profile and
inspects keys and JS expressions this project wrote -- system-minted
configuration text, not anything a person said, so it sits outside the
no-matching rule.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROFILE = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "dsh"
    / "profile"
    / "cordis.patch.yml"
)

TASK_BOARD_MODULE = "fateforger.slack_bot.task_board_mcp"
GATE_VARIABLE = "FF_TASK_TOOLS"


class _Js(str):
    """A `!!js` scalar kept as its source text so we can inspect the expression."""


def _js_constructor(loader, node):
    return _Js(loader.construct_scalar(node))


def _load_profile():
    loader = yaml.SafeLoader
    loader.add_constructor("!!js", _js_constructor)
    # PyYAML resolves `!!js` to the full tag name; register both spellings.
    loader.add_constructor("tag:yaml.org,2002:js", _js_constructor)
    return yaml.load(PROFILE.read_text(encoding="utf-8"), Loader=loader)


def _mount_rows() -> list[dict]:
    """Every row of the `insert:` list that carries the server mounts."""
    for entry in _load_profile():
        if not isinstance(entry, dict):
            continue
        rows = entry.get("insert")
        if isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("id") == "mcp-planning-result"
            for row in rows
        ):
            return [row for row in rows if isinstance(row, dict)]
    raise AssertionError("no insert: block holds the mcp-planning-result row")


def _row_named(server_name: str) -> dict:
    for row in _mount_rows():
        config = row.get("config")
        if isinstance(config, dict) and config.get("serverName") == server_name:
            return row
    raise AssertionError(f"no mount declares serverName {server_name!r}")


def test_the_task_board_server_is_mounted():
    """Without a row the subagent has no board and falls back to guessing."""
    assert _row_named("task_board")["config"]["transport"] == "stdio"


def test_the_mount_runs_the_task_board_module():
    """A mount pointing at the wrong module fails at boot, not at call time."""
    args = _row_named("task_board")["config"]["args"]
    assert TASK_BOARD_MODULE in args, args


def test_the_mount_is_gated_on_the_task_tools_variable():
    """Ungated, both tool schemas reach every planning call's preamble."""
    disabled = _row_named("task_board")["disabled"]
    assert isinstance(disabled, _Js), (
        f"disabled must be a !!js expression, got {disabled!r}"
    )
    assert GATE_VARIABLE in disabled, disabled


def test_the_task_board_row_follows_the_planning_result_row():
    """Placement is the file's own filing system; the row belongs with 3c."""
    ids = [row.get("id") for row in _mount_rows()]
    assert ids.index("mcp-task-board") == ids.index("mcp-planning-result") + 1, ids
