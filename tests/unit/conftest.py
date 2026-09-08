"""Fixtures shared by the unit suite.

Two unrelated things live here because both are "parse something once, share
it everywhere" fixtures and neither deserves its own file yet.

## Board isolation

`TaskBoard.from_settings()` builds a board against the real Notion MCP
container as soon as a token is present, and the host now calls it inside every
candidate planning turn whose session asked for anything. On a machine with no
token — this worktree, and CI — that refuses on its own and every test takes
the loud, non-blocking path by accident. On Hugo's machine, with `.env`
loaded, the same tests would reach Notion over the network.

Isolation that holds only when a credential is missing is not isolation, so it
is asserted here instead: every unit test gets a board that refuses, and a test
that means to exercise the real constructor says so with the `real_task_board`
marker.

## The profile loader

Reading `infra/dsh/profile/cordis.patch.yml`. Three files needed the profile
parsed and all three grew their own copy of the same loader, which is how the
two defects below shipped three times over (#327).

**The loader is a subclass.** `yaml.SafeLoader.add_constructor(...)` mutates the
class every other `yaml.safe_load` in the process goes through, so a helper that
registers a tag on it changes what an unrelated test parses, in whatever order
the session happens to run. A subclass carries the constructor and leaves the
shared class alone.

**The tag is the full one.** PyYAML expands the `!!x` shorthand to
`tag:yaml.org,2002:x` while composing the node, before it looks a constructor
up, so a constructor registered under the literal string `"!!js"` is filed under
a key nothing ever asks for -- the load fails with "could not determine a
constructor for the tag 'tag:yaml.org,2002:js'" and the registration reads as if
it worked. Pinned in `test_dsh_profile_env_defaults.py`.

Reading this file is inspection of configuration text this project wrote --
keys, ids and JS expressions the repo minted -- not a judgement about anything a
person said, so it sits outside the no-matching rule (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from fateforger.agents.tasks.board import TaskBoardUnavailable


@pytest.fixture(autouse=True)
def refuse_the_live_task_board(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No unit test reaches Notion, whatever the environment holds."""

    if request.node.get_closest_marker("real_task_board") is not None:
        return

    def _refuse():
        raise TaskBoardUnavailable("the task board is not reachable from a unit test")

    monkeypatch.setattr(
        "fateforger.agents.tasks.board.TaskBoard.from_settings", staticmethod(_refuse)
    )


PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "dsh"
    / "profile"
    / "cordis.patch.yml"
)

#: The tag a `!!js` scalar actually arrives under, once PyYAML has expanded the
#: shorthand. Registering `"!!js"` instead registers a key nothing looks up.
JS_TAG = "tag:yaml.org,2002:js"


class Js(str):
    """A `!!js` scalar kept as its source text, so a test can read the expression."""


class ProfileLoader(yaml.SafeLoader):
    """This suite's own loader, so `yaml.SafeLoader` is never mutated."""


def _construct_js(loader: yaml.SafeLoader, node: yaml.Node) -> Js:
    return Js(loader.construct_scalar(node))


ProfileLoader.add_constructor(JS_TAG, _construct_js)


@dataclass(frozen=True)
class LoadedProfile:
    """The parsed profile, plus the two things a test needs to ask about it.

    `is_js` and `js` are methods rather than an exported class because pytest
    runs under `--import-mode=importlib`: a test module cannot `from conftest
    import Js`, so the fixture has to hand over everything the tests need.
    """

    tree: Any

    @staticmethod
    def is_js(value: Any) -> bool:
        """True for a value that was a `!!js` expression in the profile."""
        return isinstance(value, Js)

    @staticmethod
    def js(source: str) -> Js:
        """A `!!js` scalar for a synthetic tree, so a test can build one."""
        return Js(source)


@pytest.fixture
def profile() -> LoadedProfile:
    """`cordis.patch.yml`, parsed, with `!!js` expressions kept as their source."""
    return LoadedProfile(
        tree=yaml.load(PROFILE_PATH.read_text(encoding="utf-8"), Loader=ProfileLoader)
    )
