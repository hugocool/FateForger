"""Every surface interpreter is built on one client, from one row of the
factory table, and no site builds one any other way."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import fateforger.llm.factory as factory


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setattr(factory.settings, "llm_provider", "openrouter")
    monkeypatch.setattr(factory.settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(factory.settings, "openrouter_default_model_flash", "flash/pin:nitro")
    monkeypatch.setattr(factory.settings, "openrouter_default_model_pro", "pro/pin:nitro")
    monkeypatch.setattr(factory.settings, "llm_model_intent_interpreter", "")
    monkeypatch.setattr(factory.settings, "llm_reasoning_effort_intent_interpreter", "")
    monkeypatch.setattr(factory.settings, "llm_max_tokens_intent_interpreter", -1)
    monkeypatch.setattr(factory.settings, "llm_max_tokens", 0)


def _kwargs(monkeypatch):
    captured: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(factory, "OpenAIChatCompletionClient", _Client)
    return captured


def _assert_carries_the_landed_default(captured: dict) -> None:
    """The kwargs say what the constant says -- whatever the constant says.

    Asserted against the constant rather than a literal so the two cannot
    drift, and branched rather than fixed so a later bench that moves the cap
    moves these tests with it instead of breaking them. What the cap *is* is
    pinned once, in `test_the_landed_default_is_uncapped`.
    """

    if factory._INTENT_INTERPRETER_MAX_TOKENS is None:
        assert "max_tokens" not in captured
    else:
        assert captured["max_tokens"] == factory._INTENT_INTERPRETER_MAX_TOKENS


def test_the_landed_default_is_uncapped():
    """No cap, and the bench is why.

    `scripts/bench/results-interpreter-tier-2026-09-06.md`, six configurations
    over the three interpreter evals at n=8 per case: 1024 truncated three
    draws and 2048 truncated four, out of ~816 each, on both pins, while
    neither uncapped configuration had a draw stopped by a provider limit. A
    cap does not turn #325's runaway back into an answer; it turns a long
    answer into a lost one. Spec §3's rule lands on uncapped.

    Changing this line without a new bench beside it is the thing this test
    exists to stop.
    """

    assert factory._INTENT_INTERPRETER_MAX_TOKENS is None


def test_the_default_row_is_the_flash_pin_at_minimal(openrouter, monkeypatch):
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["model"] == "flash/pin:nitro"
    assert captured["extra_body"] == {"reasoning": {"effort": "minimal"}}
    _assert_carries_the_landed_default(captured)
    # No sampling pin: CLAUDE.md retired temperature=0 on measurement, and this
    # is now the only place the interpreter's full kwargs are in hand.
    assert "temperature" not in captured


def test_the_env_overrides_each_column(openrouter, monkeypatch):
    monkeypatch.setattr(factory.settings, "llm_model_intent_interpreter", "other/model:nitro")
    monkeypatch.setattr(factory.settings, "llm_reasoning_effort_intent_interpreter", "high")
    monkeypatch.setattr(factory.settings, "llm_max_tokens_intent_interpreter", 2048)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["model"] == "other/model:nitro"
    assert captured["extra_body"] == {"reasoning": {"effort": "high"}}
    assert captured["max_tokens"] == 2048


def test_zero_means_uncapped_and_minus_one_means_the_default(openrouter, monkeypatch):
    # The constant is pinned to a number here on purpose. The landed default is
    # uncapped, so against it the two sentinels build the identical client and
    # this test could not tell a working -1 from one that had stopped reading
    # the constant at all -- it would pass either way. A number makes the
    # difference observable, which is the whole of what the test is for.
    monkeypatch.setattr(factory, "_INTENT_INTERPRETER_MAX_TOKENS", 1234)
    monkeypatch.setattr(factory.settings, "llm_max_tokens_intent_interpreter", 0)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert "max_tokens" not in captured
    monkeypatch.setattr(factory.settings, "llm_max_tokens_intent_interpreter", -1)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["max_tokens"] == 1234


def test_the_global_cap_does_not_leak_into_the_interpreter_row(openrouter, monkeypatch):
    # llm_max_tokens is the everything-else cap; the interpreter has its own column.
    monkeypatch.setattr(factory.settings, "llm_max_tokens", 9999)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    _assert_carries_the_landed_default(captured)


def test_the_runtime_site_builds_on_the_interpreter_client(monkeypatch):
    import fateforger.core.runtime as runtime_module
    calls: list[str] = []
    monkeypatch.setattr(
        runtime_module, "build_intent_interpreter_client", lambda: calls.append("built") or object()
    )
    runtime_module._build_timeboxing_intent_interpreter()
    assert calls == ["built"]


def test_the_planning_site_builds_on_the_interpreter_client(monkeypatch):
    import fateforger.slack_bot.planning as planning_module
    from fateforger.slack_bot.planning import PlanningCoordinator
    calls: list[str] = []
    monkeypatch.setattr(
        planning_module, "build_intent_interpreter_client", lambda: calls.append("built") or object()
    )
    coordinator = PlanningCoordinator.__new__(PlanningCoordinator)
    coordinator._intent_interpreter = None
    coordinator._ensure_intent_interpreter()
    assert calls == ["built"]


_SRC = Path(__file__).resolve().parents[2] / "src" / "fateforger"

_INTERPRETERS = {"SurfaceIntentInterpreter", "TimeboxingIntentInterpreter"}
_THE_ONE_BUILDER = "build_intent_interpreter_client"


def _called_name(node: ast.Call) -> str | None:
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    current = parents.get(node)
    while current is not None and not isinstance(
        current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)
    ):
        current = parents.get(current)
    return current


def _last_assigned_value(name: str, scope: ast.AST, before: int) -> ast.expr | None:
    """The value last bound to ``name`` in this scope before line ``before``.

    A name with no assignment in scope -- a parameter, i.e. a client the
    function was handed rather than built -- resolves to nothing, and the
    caller treats that as clean: the construction site is wherever the
    argument was built, not wherever it was passed along.
    """
    found: ast.stmt | None = None
    for stmt in ast.walk(scope):
        if isinstance(stmt, ast.Assign):
            targets: list[ast.expr] = list(stmt.targets)
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        if value is None or stmt.lineno >= before:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            if found is None or stmt.lineno > found.lineno:
                found = stmt
    if found is None:
        return None
    return found.value  # type: ignore[return-value]


def test_no_site_under_src_builds_an_interpreter_on_another_agents_client():
    """The guard: an interpreter's client comes from build_intent_interpreter_client
    and nowhere else.

    Walks every module for a call to SurfaceIntentInterpreter or
    TimeboxingIntentInterpreter and inspects the client it is handed --
    following a local variable back to its last assignment, because the site
    this ticket exists to fix (runtime.py) builds the client on one line and
    passes it on the next. Anything the argument resolves to that is a call to
    something other than build_intent_interpreter_client is an offender:
    build_autogen_chat_client, build_langchain_chat_openai and a bare
    OpenAIChatCompletionClient are all equally another agent's client.

    Identifiers this system minted; no user content.
    """
    assert _SRC.is_dir(), f"the guard found no source tree at {_SRC}"
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _called_name(node) not in _INTERPRETERS:
                continue
            scope = _enclosing_scope(node, parents)
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                resolved = arg
                if isinstance(resolved, ast.Name) and scope is not None:
                    resolved = _last_assigned_value(resolved.id, scope, node.lineno)
                if isinstance(resolved, ast.Call) and _called_name(resolved) != _THE_ONE_BUILDER:
                    offenders.append(
                        f"{path.relative_to(_SRC)}:{node.lineno} <- {_called_name(resolved)}"
                    )
    assert offenders == []
