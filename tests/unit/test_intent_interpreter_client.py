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


def test_the_landed_cap_is_the_bench_ruling():
    """1024, on Hugo's ruling over the 2026-09-06 bench.

    `scripts/bench/results-interpreter-tier-2026-09-06.md` (six configurations,
    three evals, n=8 per case) with the reading in the `.reading.md` sidecar:
    1024 truncated 3 of the 545 draws taken at that cap and 2048 truncated 4 of
    546, so spec §3's rule lands on uncapped -- and was overruled, because the
    seven truncated draws were runaways stopped, not answers lost. Every one
    ran 6-56s against a 1-2s median, the largest legitimate uncapped answer on
    the pro pin -- the pin this row runs on -- was 405 tokens, 2048 cut more
    draws than 1024 without buying one back, and no case failed on length. The
    405 does not carry to the flash pin: `flash-minimal` completed a
    4,839-token draw in a case it still scored 8/8, which is #406's problem to
    re-read when it flips the pin, not this constant's today.

    Moving this line without a bench beside it is what this test exists to stop.
    """

    assert factory._INTENT_INTERPRETER_MAX_TOKENS == 1024


def test_the_default_row_is_the_pro_pin_at_low_and_capped(openrouter, monkeypatch):
    """The measured configuration, not the intended one.

    The pin: the flash pin at `minimal` is CLAUDE.md's recorded role for
    routing and is where this row is going. The 2026-09-06 bench says the
    prompts as written lose there -- 4 judgement losses against the pro pin's
    0, with revision-after-commit at 1/8 against 8/8. (The raw case counts,
    27/34 against 32/34, are not that comparison: they include the break-it
    families, which assert a flip and are never judgement losses.) So the
    default follows the measurement until the prompts are fitted to flash.

    The effort: `low`, on Hugo's ruling over the 2026-09-11 bench
    (scripts/bench/results-interpreter-tier-2026-09-11.md and its
    `.reading.md`). pro/`low` at 1024 lost no judgement pro/`high` held, and
    truncated 0 of 384 production draws against `high`'s 3 of 386 at about 18%
    less cost -- so the cheaper effort stands and `high` is not justified.
    Model, effort and cap are asserted together because pro/`low`/1024 is the
    configuration that bench built and read back.
    """

    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["model"] == "pro/pin:nitro"
    assert captured["extra_body"] == {"reasoning": {"effort": "low"}}
    assert captured["max_tokens"] == factory._INTENT_INTERPRETER_MAX_TOKENS
    assert factory._INTENT_INTERPRETER_MAX_TOKENS > 0
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
    monkeypatch.setattr(factory.settings, "llm_max_tokens_intent_interpreter", 0)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert "max_tokens" not in captured
    monkeypatch.setattr(factory.settings, "llm_max_tokens_intent_interpreter", -1)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["max_tokens"] == factory._INTENT_INTERPRETER_MAX_TOKENS


def test_the_global_cap_does_not_leak_into_the_interpreter_row(openrouter, monkeypatch):
    # llm_max_tokens is the everything-else cap; the interpreter has its own column.
    monkeypatch.setattr(factory.settings, "llm_max_tokens", 9999)
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["max_tokens"] == factory._INTENT_INTERPRETER_MAX_TOKENS


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
