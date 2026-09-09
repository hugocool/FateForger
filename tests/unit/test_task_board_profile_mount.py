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

import json
import os
import shutil
import subprocess

import pytest

# The profile is parsed by the `profile` fixture in `tests/unit/conftest.py`.

TASK_BOARD_MODULE = "fateforger.slack_bot.task_board_mcp"
GATE_VARIABLE = "FF_TASK_TOOLS"


def _mount_rows(profile) -> list[dict]:
    """Every row of the `insert:` list that carries the server mounts."""
    for entry in profile.tree:
        if not isinstance(entry, dict):
            continue
        rows = entry.get("insert")
        if isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("id") == "mcp-planning-result"
            for row in rows
        ):
            return [row for row in rows if isinstance(row, dict)]
    raise AssertionError("no insert: block holds the mcp-planning-result row")


def _row_named(profile, server_name: str) -> dict:
    for row in _mount_rows(profile):
        config = row.get("config")
        if isinstance(config, dict) and config.get("serverName") == server_name:
            return row
    raise AssertionError(f"no mount declares serverName {server_name!r}")


def test_the_task_board_server_is_mounted(profile):
    """Without a row the subagent has no board and falls back to guessing."""
    assert _row_named(profile, "task_board")["config"]["transport"] == "stdio"


def test_the_mount_runs_the_task_board_module(profile):
    """A mount pointing at the wrong module fails at boot, not at call time."""
    args = _row_named(profile, "task_board")["config"]["args"]
    assert TASK_BOARD_MODULE in args, args


def test_the_mount_is_gated_on_the_task_tools_variable(profile):
    """Ungated, both tool schemas reach every planning call's preamble."""
    disabled = _row_named(profile, "task_board")["disabled"]
    assert profile.is_js(disabled), (
        f"disabled must be a !!js expression, got {disabled!r}"
    )
    assert GATE_VARIABLE in disabled, disabled


def test_the_task_board_row_follows_the_planning_result_row(profile):
    """Placement is the file's own filing system; the row belongs with 3c."""
    ids = [row.get("id") for row in _mount_rows(profile)]
    assert ids.index("mcp-task-board") == ids.index("mcp-planning-result") + 1, ids


def test_the_mount_hands_the_child_the_parents_environment(profile):
    """The client composes a child's env, so anything unnamed may be scrubbed.

    Forwarding the whole parent env is what passes a variable that IS set and
    omits one that is not. The row is one `!!js` expression rather than a map
    of keys precisely so absence stays absence.
    """
    env = _row_named(profile, "task_board")["config"]["env"]

    assert profile.is_js(env), f"env must be one !!js expression, got {env!r}"
    assert "process.env" in env, env
    assert "PYTHONPATH" in env, env


def test_the_mount_never_forwards_a_variable_as_an_empty_string(profile):
    """An empty string is not "unset", and both readings of it were fatal.

    NOTION_MCP_URL is set nowhere here, so `process.env.NOTION_MCP_URL || ''`
    handed the child "", `Settings` refused it as not an absolute URL, the
    server died before serving a tool, and `failOnStartupError: true` took the
    whole profile down with it -- so the first turn that switched this feature
    on stopped /dsh answering at all. The token fails the other way and in
    silence: an env var set to "" shadows the dotenv value in pydantic-settings,
    so a real token in .env is read as empty and the board refuses itself.

    Pinned as an expression check because both failures need the row enabled to
    appear, and nothing else in the suite enables it.
    """
    env = _row_named(profile, "task_board")["config"]["env"]

    assert "|| ''" not in env, (
        "a `|| ''` fallback forwards an empty string where the variable is "
        f"unset, which is boot-fatal for a validated setting: {env!r}"
    )
    assert '|| ""' not in env, env



#: What the child needs out of the environment to reach the Notion container,
#: and what it does with each. The row names none of them -- it forwards the
#: whole parent environment -- so this list is what that shape has to keep
#: delivering, and the two tests below check it from both ends.
#:
#:   MCP_HTTP_AUTH_TOKEN  the bearer the facade sends; empty is fatal and silent
#:   NOTION_MCP_URL       the endpoint, when it is set anywhere
#:   MCP_HTTP_PORT        the port the endpoint DEFAULTS to when it is not
#:
#: The port was the one nobody had checked. `get_notion_mcp_url()` resolves
#: through `os.environ` alone and falls back to 3001, so a host running the
#: container anywhere else needs the variable in the child's environment or the
#: board dials the wrong port -- loudly, but a long way from the cause.
CHILD_NOTION_VARIABLES = (
    "MCP_HTTP_AUTH_TOKEN",
    "NOTION_MCP_URL",
    "MCP_HTTP_PORT",
)


def test_the_mount_names_none_of_the_notion_variables_it_forwards(profile):
    """Naming one can only shadow what the whole-env forward already delivers.

    This is the half that needs no `node`: the expression starts from
    `process.env`, so every variable that is set arrives, and any of these three
    appearing in the expression means the row went back to composing them one by
    one -- the shape that shipped `|| ''` and took the host down.
    """
    env = _row_named(profile, "task_board")["config"]["env"]

    assert "Object.assign({}, process.env" in env, env
    for name in CHILD_NOTION_VARIABLES:
        assert name not in env, (
            f"{name} is named in the mount's env expression; composing a "
            "variable by hand is what forwarded an empty string before"
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the profile")
def test_the_forwarded_env_actually_carries_the_notion_variables(profile):
    """The expression evaluated, because "it forwards everything" is a claim.

    `dsh-mcp-client` composes the child's env from this JavaScript, so the
    question is what the object holds, not what the source says. Run under node
    with a parent environment that carries all three, it must hand every one of
    them through unchanged, and add the PYTHONPATH the child imports from.
    """
    env_expression = _row_named(profile, "task_board")["config"]["env"]
    parent = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "FF_FATEFORGER_ROOT": "/somewhere/else",
        "MCP_HTTP_AUTH_TOKEN": "a-real-looking-secret",
        "NOTION_MCP_URL": "http://notion-mcp:3100/mcp",
        "MCP_HTTP_PORT": "3100",
    }

    result = subprocess.run(
        ["node", "-e", f"console.log(JSON.stringify(({env_expression})))"],
        env=parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    child = json.loads(result.stdout)
    for name in CHILD_NOTION_VARIABLES:
        assert child.get(name) == parent[name], (
            f"the child's env carries {name}={child.get(name)!r}, "
            f"not the parent's {parent[name]!r}"
        )
    assert child["PYTHONPATH"] == "/somewhere/else/src"


def _subagent_rows(profile) -> list[dict]:
    """Every `dsh-tool-subagent` instance in the mount block."""
    return [
        row
        for row in _mount_rows(profile)
        if row.get("name") == "@deepseek-ai/dsh-tool-subagent"
    ]


def test_every_subagent_may_actually_be_started(profile):
    """`maxDepth` counts the child's own depth, so 0 refuses every call.

    `resolveChildDepth` computes `delegationDepthOf(parent) + 1` and refuses
    when that exceeds `maxDepth`. The root is depth 0, so its child is depth 1
    and `1 > 0` refuses. `timebox_patch` carried 0 to mean "may not delegate
    onward" and was therefore uninvokable for as long as the row existed --
    measured 2026-09-08: the tool answers "subagent depth exceeds maxDepth (0)"
    and no candidate basis is captured. 1 is what that intent costs: the child
    runs, and its own children are depth 2 and refused.

    Read as a defect the day it reappears, because nothing else does: a row
    that cannot start looks exactly like a model that chose not to call it.
    """
    rows = _subagent_rows(profile)
    assert rows, "no dsh-tool-subagent rows found; has the mount block moved?"
    for row in rows:
        depth = row["config"]["maxDepth"]
        if depth == "provider-managed":
            continue
        assert depth >= 1, (
            f"{row.get('id')!r} carries maxDepth {depth}; a child is its "
            f"parent's depth plus one, so anything below 1 refuses every call"
        )
