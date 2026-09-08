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


def test_the_default_row_is_the_flash_pin_at_minimal_and_capped(openrouter, monkeypatch):
    captured = _kwargs(monkeypatch)
    factory.build_intent_interpreter_client()
    assert captured["model"] == "flash/pin:nitro"
    assert captured["extra_body"] == {"reasoning": {"effort": "minimal"}}
    assert captured["max_tokens"] == factory._INTENT_INTERPRETER_MAX_TOKENS
    assert factory._INTENT_INTERPRETER_MAX_TOKENS > 0


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


def test_no_site_under_src_builds_an_interpreter_on_another_agents_client():
    """The guard: an interpreter's client comes from build_intent_interpreter_client
    and nowhere else. Walks every module for a call to SurfaceIntentInterpreter or
    TimeboxingIntentInterpreter whose first argument is build_autogen_chat_client(...)
    with any agent type. Identifiers this system minted; no user content."""
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in {"SurfaceIntentInterpreter", "TimeboxingIntentInterpreter"}:
                continue
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Call):
                    inner = getattr(arg.func, "id", None) or getattr(arg.func, "attr", None)
                    if inner == "build_autogen_chat_client":
                        offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == []
