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

from pathlib import Path

import yaml

PROFILE = Path(__file__).resolve().parents[2] / "infra" / "dsh" / "profile" / "cordis.patch.yml"


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


def test_every_env_entry_read_from_the_process_has_a_fallback():
    """An `env:` value that can evaluate to undefined fails the profile at boot."""
    tree = _load_profile()
    offenders: list[str] = []
    for path, env in _env_blocks(tree):
        for name, value in env.items():
            if isinstance(value, _Js) and "process.env." in value and "||" not in value:
                offenders.append(f"{'/'.join(path)}/{name}: {value.strip()}")
    assert not offenders, (
        "these env entries become `undefined` when the variable is unset, and "
        "dsh-mcp-client rejects the mount, which fails the whole profile:\n  "
        + "\n  ".join(offenders)
    )


def test_the_profile_actually_has_env_blocks_to_check():
    """Guards the guard: an empty scan would pass while proving nothing."""
    assert list(_env_blocks(_load_profile())), "no env: blocks found; the scan is vacuous"
