"""Which interpreter the DSH hooks run under.

Every hook was `${CLAUDE_PROJECT_DIR}/.venv/bin/python -m ...`, and the profile
sets `projectDir` to `FF_FATEFORGER_ROOT`. A git worktree has no `.venv`, so from
a worktree every hook failed silently: no candidate capture, no commit gate, no
attempt guard. Nothing surfaced, because a hook that cannot start is
indistinguishable from one with nothing to say.

Measured live on 2026-08-30. The damage showed three layers away as a
`validated_candidate` carrying no snapshot, patch or digest -- a plan that could
be approved and never committed.

The interpreter that runs the host is the one that can run the host's hooks, so
the bridge exports its own. The `${CLAUDE_PROJECT_DIR}` default is kept for any
caller that sets nothing, which is exactly today's behaviour.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "infra" / "dsh" / "hooks.json"

FF_HOOK_PYTHON_ENV = "FF_HOOK_PYTHON"


def _commands() -> list[str]:
    config = json.loads(HOOKS.read_text(encoding="utf-8"))["hooks"]
    return [
        hook["command"]
        for groups in config.values()
        for group in groups
        for hook in group["hooks"]
    ]


def test_every_hook_accepts_an_interpreter_that_exists() -> None:
    """Catches a hook pinned to a venv the checkout does not have."""

    commands = _commands()
    assert commands, "no hooks configured"
    for command in commands:
        assert command.startswith(
            "${FF_HOOK_PYTHON:-${CLAUDE_PROJECT_DIR}/.venv/bin/python}"
        ), command


def test_the_bridge_exports_the_interpreter_running_it() -> None:
    """The host's own interpreter provably has the host's dependencies."""

    from fateforger.slack_bot import harness_bridge

    assert harness_bridge.HOOK_PYTHON_ENV == FF_HOOK_PYTHON_ENV
    assert harness_bridge._hook_interpreter() == sys.executable
