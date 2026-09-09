"""The harness profile must boot with no FF_DSH_* set, or e2e by hand is impossible.

On 2026-09-03 the profile refused to load outside a Slack turn:

    failed to apply loader entry mcp-timebox-progress: invalid config
    failed to apply loader entry mcp-planning-result:  invalid config

`dsh-mcp-client` types a mount's `env` as a dict of strings. Three entries were
filled straight from `process.env` with no fallback, so an unset variable became
`undefined`, which is not a string, which failed the whole profile. The error's
"but got {...}" looked valid because JSON drops undefined keys.

This test reads the versioned profile and pins the invariant that let it ship:
every `process.env.X` inside an `env:` block carries a fallback. It is a check on
system-minted configuration text -- keys and JS expressions this project wrote --
not on anything a person said, so it sits outside the no-matching rule.
"""

from __future__ import annotations

import pytest
import yaml

# The profile is parsed by the `profile` fixture in `tests/unit/conftest.py`;
# `!!js` scalars arrive as strings that `profile.is_js` recognises.


def _env_blocks(node, path=()):
    """Yield (path, mapping) for every `env:` mapping anywhere in the tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                yield path + (key,), value
            yield from _env_blocks(value, path + (str(key),))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _env_blocks(item, path + (str(i),))


def test_every_env_entry_read_from_the_process_has_a_fallback(profile):
    """An `env:` value that can evaluate to undefined fails the profile at boot."""
    offenders: list[str] = []
    for path, env in _env_blocks(profile.tree):
        for name, value in env.items():
            if profile.is_js(value) and "process.env." in value and "||" not in value:
                offenders.append(f"{'/'.join(path)}/{name}: {value.strip()}")
    assert not offenders, (
        "these env entries become `undefined` when the variable is unset, and "
        "dsh-mcp-client rejects the mount, which fails the whole profile:\n  "
        + "\n  ".join(offenders)
    )


def test_the_profile_actually_has_env_blocks_to_check(profile):
    """Guards the guard: an empty scan would pass while proving nothing."""
    assert list(_env_blocks(profile.tree)), "no env: blocks found; the scan is vacuous"


def test_loading_the_profile_leaves_the_shared_safeloader_alone(profile) -> None:
    """A helper that registers a tag on `yaml.SafeLoader` changes every load.

    The three copies of this loader all called `yaml.SafeLoader.add_constructor`,
    which mutates the class shared by every `yaml.safe_load` in the process. A
    `!!js` scalar in some other file then parses as a `Js` string instead of
    failing, or not, depending on whether this module ran first -- an
    order-dependent parser is not one anybody can reason about.
    """
    _ = profile.tree

    # The tag PyYAML resolves `!!js` to; see the fixture's module docstring.
    assert "tag:yaml.org,2002:js" not in yaml.SafeLoader.yaml_constructors


def test_the_js_shorthand_alone_constructs_nothing() -> None:
    """Why the fixture registers the resolved tag and not the `!!js` spelling.

    Registering the shorthand looks like it worked and is never consulted: the
    node reaches the constructor lookup already carrying the full tag.
    """

    class _ShorthandOnly(yaml.SafeLoader):
        pass

    _ShorthandOnly.add_constructor("!!js", lambda loader, node: node.value)

    with pytest.raises(yaml.constructor.ConstructorError) as excinfo:
        yaml.load("gate: !!js 'process.env.X'", Loader=_ShorthandOnly)

    assert "tag:yaml.org,2002:js" in str(excinfo.value)
