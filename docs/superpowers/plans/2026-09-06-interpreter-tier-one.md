# Interpreter Tier One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every surface interpreter is built by one named function on its own factory row (flash pin, `minimal`, capped), the switch and the cap are benched against today's client on the three evals, and the cap default is set by the numbers.

> **Superseded, on the bench this plan asked for.** "Flash pin, `minimal`" was the plan going in; the row landed on the **pro pin at `high`, capped at 1024** (effort `low` since 2026-09-11, on Hugo's ruling over `scripts/bench/results-interpreter-tier-2026-09-11.md`) — Hugo's ruling over `scripts/bench/results-interpreter-tier-2026-09-06.md` and its `.reading.md` sidecar, because the prompts as written lose 4 judgements to 0 on flash. The flash pin is still the destination and waits on #406. Step 5 below carries the same note; `docs/reference/setup/llm.md`'s "Surface interpreter model" section is what an incoming reader should read instead of this header.

**Architecture:** A new `intent_interpreter` agent type in `llm/factory.py`'s per-agent table, mirroring `timeboxing_judge`; `build_intent_interpreter_client()` is the only way an interpreter gets a client. A bench script runs the existing eval files under a six-configuration matrix through a pytest plugin that wraps the OpenAI client's `create` to record latency, usage, finish reason and errors per draw; results land in `scripts/bench/` beside the 2026-08-24 decision.

**Tech Stack:** Python 3.11, Pydantic settings, AutoGen `OpenAIChatCompletionClient`, pytest (+ a `-p` plugin), OpenRouter, `scripts/bench/report.py`'s pricing helper.

**Spec:** `docs/superpowers/specs/2026-09-06-interpreter-tier-one-design.md`. **Ticket:** #336 (fixes #325).

## Global Constraints

- **No `.env` change, ever, by an agent.** Configuration for the bench travels as process environment set by the bench script for its subprocesses only. The code default for the interpreter's model is the flash pin — CLAUDE.md's recorded role for routing, not a new pin. **Superseded by the bench (see the note under Goal):** the landed code default is the pro pin at `high` (at `low` since Hugo's 2026-09-11 ruling over `scripts/bench/results-interpreter-tier-2026-09-11.md`). `.env` was untouched either way, so the no-`.env`-change constraint held.
- **No keyword matching, string matching, or regex on user content.** The guard test compares identifiers this system minted (function names, agent-type strings).
- **Never pin `temperature`.** Never assert an exact model output string in a unit test.
- **Worktree discipline.** All work in `.worktrees/interpreter-tier-one` on `feat/336-interpreter-tier-one`. Every pytest run: `PYTHONPATH=src ../../.venv/bin/python -m pytest …` from the worktree root — without `PYTHONPATH=src` the venv imports the parent checkout's unchanged `src`. `.env` is absent in the worktree: for anything that reaches OpenRouter, `cp ../../.env .env` once and `set -a; source .env; set +a` in the shell; `git status` must never show `.env`.
- **Package suite before reporting done:** `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q`. `tests/e2e/test_slack_handoff_flow.py::test_slack_handoff_sets_focus_and_forwards` is pre-existing red; anything else red is yours.
- **Commit style:** `<type>(<scope>): <lowercase sentence> (#336)`, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Name files in `git add`; never `-A`. Commit in the worktree only.
- **The bench never folds transport into judgement.** A draw that raised is counted by its error class, separately from decision counts.
- **Map #333's constraints:** least code that holds the invariant; the second surface must get the client for free; say in the report what the next interpreter costs.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/fateforger/core/config.py` | three `intent_interpreter` settings fields + validator entry | 1 |
| `src/fateforger/llm/factory.py` | `INTENT_INTERPRETER`, three table rows, `_INTENT_INTERPRETER_MAX_TOKENS`, `build_intent_interpreter_client()` | 1 |
| `src/fateforger/core/runtime.py` | `_build_timeboxing_intent_interpreter` uses the site | 1 |
| `src/fateforger/slack_bot/planning.py` | `_ensure_intent_interpreter` uses the site | 1 |
| `tests/integration/test_eval_planning_card_intent.py` | uses the site | 1 |
| `tests/integration/test_eval_day_frame.py` | uses `timeboxing_judge` (production's client for that judge) | 1 |
| `tests/unit/test_intent_interpreter_client.py` | resolution, sites, guard | 1 |
| `scripts/bench/_interpreter_tier_plugin.py` | pytest plugin: per-draw instrumentation to JSONL | 2 |
| `scripts/bench/interpreter_tier.py` | the matrix runner + summariser | 2 |
| `scripts/bench/results-interpreter-tier-2026-09-06.json`, `.md` | the numbers | 2 |
| `src/fateforger/llm/factory.py` (`_INTENT_INTERPRETER_MAX_TOKENS`) | set from the bench | 2 |

---

## Task 1: The `intent_interpreter` row and its construction site (#336)

**Files:**
- Modify: `src/fateforger/core/config.py` (~lines 79-124: the `llm_model_*`, `llm_reasoning_effort_*`, `llm_max_tokens*` fields; the validator at ~293)
- Modify: `src/fateforger/llm/factory.py` (`_model_for_agent` ~141, `_reasoning_effort_for_agent` ~188, `_max_tokens_for_agent` ~194; add the constant and the function after `build_autogen_chat_client`)
- Modify: `src/fateforger/core/runtime.py:503-513`, `src/fateforger/slack_bot/planning.py:169-175`, `tests/integration/test_eval_planning_card_intent.py:74`, `tests/integration/test_eval_day_frame.py:55`
- Create: `tests/unit/test_intent_interpreter_client.py`

**Interfaces:**
- Produces: `fateforger.llm.factory.INTENT_INTERPRETER = "intent_interpreter"`; `fateforger.llm.factory.build_intent_interpreter_client() -> OpenAIChatCompletionClient`; `fateforger.llm.factory._INTENT_INTERPRETER_MAX_TOKENS: int` (module constant, initial value `1024`, set by Task 2); settings `llm_model_intent_interpreter: str = ""`, `llm_reasoning_effort_intent_interpreter: str = ""`, `llm_max_tokens_intent_interpreter: int = -1` where `-1` means "use the code default", `0` means "uncapped", `>0` a cap. Environment names: `LLM_MODEL_INTENT_INTERPRETER`, `LLM_REASONING_EFFORT_INTENT_INTERPRETER`, `LLM_MAX_TOKENS_INTENT_INTERPRETER`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_intent_interpreter_client.py`:

```python
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
```

Check the factory's existing tests (`tests/unit/test_llm_factory*.py` or wherever `build_autogen_chat_client` is tested) for how they stub `OpenAIChatCompletionClient` and `settings`, and match that shape if it differs from the `_kwargs` helper above — the assertions stay.

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_intent_interpreter_client.py -q`
Expected: FAIL — `AttributeError: module 'fateforger.llm.factory' has no attribute 'build_intent_interpreter_client'` and the settings attributes missing.

- [ ] **Step 3: The settings fields**

In `core/config.py`, beside the other per-agent fields:

```python
    llm_model_intent_interpreter: str = Field(default="")
```
under the model block;
```python
    llm_reasoning_effort_intent_interpreter: str = Field(default="")
```
under the reasoning-effort block; and
```python
    #: -1: the code default (see llm/factory._INTENT_INTERPRETER_MAX_TOKENS).
    #: 0: uncapped. >0: the cap. The interpreter answers a small fixed schema;
    #: uncapped it ran away to 16,384 tokens on ~5% of calls (#325).
    llm_max_tokens_intent_interpreter: int = Field(default=-1)
```
under the max-tokens block. Do **not** add it to `_validate_non_negative_tokens` (−1 is legal here); add a separate validator that rejects values below −1.

- [ ] **Step 4: The factory row and the function**

In `llm/factory.py`:

(a) `_model_for_agent`, after the `timeboxing_judge` branch:
```python
    if agent_type == INTENT_INTERPRETER:
        # Every surface interpreter: choosing among listed options is term
        # typing on the flash pin (CLAUDE.md "Every route is a judgement").
        # Its own row so it stops inheriting its host agent's client, which is
        # how the routing seam ended up on the pro pin at high and ran away (#325).
        return pick(
            settings.llm_model_intent_interpreter,
            openai=openai_default,
            openrouter=openrouter_flash,
        )
```
(b) `_reasoning_effort_for_agent`, after the `timeboxing_judge` branch:
```python
    if agent_type == INTENT_INTERPRETER:
        return normalize(settings.llm_reasoning_effort_intent_interpreter) or "minimal"
```
(c) `_max_tokens_for_agent`, before the final `return normalize(settings.llm_max_tokens)`:
```python
    if agent_type == INTENT_INTERPRETER:
        configured = settings.llm_max_tokens_intent_interpreter
        if configured == -1:
            return _INTENT_INTERPRETER_MAX_TOKENS
        return normalize(configured)
```
(d) module-level, near the top with the other constants:
```python
INTENT_INTERPRETER = "intent_interpreter"

#: The interpreter's answer is a small fixed schema (~120 bytes; a few hundred
#: tokens with several facts and a revision instruction). Uncapped, the pro pin
#: ran to 16,384 tokens on ~5% of calls (#325). Set by the bench in
#: scripts/bench/results-interpreter-tier-2026-09-06.md; 1024 until then.
_INTENT_INTERPRETER_MAX_TOKENS = 1024
```
(e) after `build_autogen_chat_client`:
```python
def build_intent_interpreter_client() -> OpenAIChatCompletionClient:
    """The one client every surface interpreter is built on.

    Tier one: choosing among listed options is term typing, not deliberation
    (CLAUDE.md "Every route is a judgement"). One row, one function, so the
    next surface gets the same client for free and no interpreter inherits
    whatever its host agent happens to run on.
    """
    return build_autogen_chat_client(INTENT_INTERPRETER)
```
Add both names to `__all__` if the module has one.

- [ ] **Step 5: Switch the sites**

`core/runtime.py:_build_timeboxing_intent_interpreter`: `model_client = build_intent_interpreter_client()` (import it from `fateforger.llm.factory`; keep the docstring's temperature note).
`slack_bot/planning.py:_ensure_intent_interpreter`: `SurfaceIntentInterpreter(build_intent_interpreter_client())`.
`tests/integration/test_eval_planning_card_intent.py:74`: `SurfaceIntentInterpreter(build_intent_interpreter_client())` (adjust the import at :59).
`tests/integration/test_eval_day_frame.py:55`: `return build_autogen_chat_client("timeboxing_judge")` with a one-line comment: production runs this judge on `timeboxing_judge_model_client` (`timeboxing_host.py:~223`); the eval measures that client.

- [ ] **Step 6: Run the new tests, the factory's tests, then the package suite**

Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_intent_interpreter_client.py -q` → PASS.
Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit -k "factory or llm or planning_surface or timeboxing_intents" -q` → PASS.
Run: `PYTHONPATH=src ../../.venv/bin/python -m pytest tests -m "not slow" -q` → only the pre-existing failure.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/core/config.py src/fateforger/llm/factory.py src/fateforger/core/runtime.py src/fateforger/slack_bot/planning.py tests/integration/test_eval_planning_card_intent.py tests/integration/test_eval_day_frame.py tests/unit/test_intent_interpreter_client.py
git commit -m "feat(llm): every surface interpreter is built on one client, on tier one's row (#336)

An intent_interpreter row in the factory table -- flash pin, minimal, capped --
and build_intent_interpreter_client() as the only way an interpreter gets a
client. The runtime and planning-card sites switch to it; the day-frame eval
switches to the judge client production actually uses. A guard test refuses
any interpreter built on another agent's client.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Task 2: The bench, and the cap it picks (#336, #325)

**Files:**
- Create: `scripts/bench/_interpreter_tier_plugin.py`
- Create: `scripts/bench/interpreter_tier.py`
- Create: `scripts/bench/results-interpreter-tier-2026-09-06.json`, `scripts/bench/results-interpreter-tier-2026-09-06.md`
- Modify: `src/fateforger/llm/factory.py` (`_INTENT_INTERPRETER_MAX_TOKENS`, only if the bench moves it)

**Interfaces:**
- Consumes: `LLM_MODEL_INTENT_INTERPRETER`, `LLM_REASONING_EFFORT_INTENT_INTERPRETER`, `LLM_MAX_TOKENS_INTENT_INTERPRETER` (Task 1); `scripts/bench/report.py:pricing(models) -> dict[model, (in_per_M, out_per_M)]`; the three eval files (two in this worktree, `test_eval_timebox_question.py` in `../asked-not-started`, which takes `LLM_MODEL_TIMEBOXING` / `LLM_REASONING_EFFORT_TIMEBOXING` / `LLM_MAX_TOKENS`).
- Produces: the two results files; the final `_INTENT_INTERPRETER_MAX_TOKENS`.

- [ ] **Step 1: The plugin**

Create `scripts/bench/_interpreter_tier_plugin.py`:

```python
"""pytest plugin: record every model call the evals make, one JSON line each.

Loaded with `-p _interpreter_tier_plugin` (scripts/bench on sys.path). Wraps
`OpenAIChatCompletionClient.create` so the eval files need no change: latency,
usage, finish reason, and the exception class when the call raised, plus the
test node the draw belonged to. Output file from INTERPRETER_TIER_BENCH_OUT.
"""

from __future__ import annotations

import json
import os
import time

import pytest

_OUT = os.environ.get("INTERPRETER_TIER_BENCH_OUT")
_CONFIG = os.environ.get("INTERPRETER_TIER_CONFIG", "")
_current_node: dict[str, str] = {"id": ""}


def pytest_runtest_setup(item):
    _current_node["id"] = item.nodeid


@pytest.fixture(autouse=True, scope="session")
def _wrap_create():
    if not _OUT:
        yield
        return
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    original = OpenAIChatCompletionClient.create

    async def create(self, *args, **kwargs):
        started = time.perf_counter()
        row = {"config": _CONFIG, "node": _current_node["id"], "model": getattr(self, "_raw_config", {}).get("model") if hasattr(self, "_raw_config") else None}
        try:
            result = await original(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised unchanged
            row.update({"latency_s": time.perf_counter() - started, "error": type(exc).__name__})
            raise
        else:
            usage = getattr(result, "usage", None)
            row.update({
                "latency_s": time.perf_counter() - started,
                "finish_reason": getattr(result, "finish_reason", None),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
            })
            return result
        finally:
            with open(_OUT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

    OpenAIChatCompletionClient.create = create
    try:
        yield
    finally:
        OpenAIChatCompletionClient.create = original
```

Find how the client exposes its model name (read `autogen_ext.models.openai._openai_client.OpenAIChatCompletionClient.__init__` in the venv for the attribute that holds `create_args["model"]`) and use that instead of the `_raw_config` guess; the model per row is required output.

- [ ] **Step 2: The runner**

Create `scripts/bench/interpreter_tier.py`:

```python
#!/usr/bin/env python3
"""Tier one on tier one's pin: bench the surface interpreters (#336, #325).

Runs the three interpreter evals, n=8 per case, under six configurations --
today's client (pro pin, high) and the flash pin at minimal, each uncapped and
capped at 1024 and 2048 -- and records per draw: latency, tokens, finish
reason, error class. Per case: decision counts and transport losses, kept
apart. Per configuration: cases passed, lowest case, losses, median/p90
latency, median completion tokens, cost from OpenRouter pricing.

Configurations reach the code as process environment for each pytest
subprocess only; .env is never written. The pro and flash pins race each
other; within one pin the three files run one after another so a pin's
latency is not measuring contention with itself (model_bench.py's rule).

    set -a; source .env; set +a
    PYTHONPATH=src ../../.venv/bin/python scripts/bench/interpreter_tier.py --date 2026-09-06
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKTREE = HERE.parents[1]
PEER_WORKTREE = WORKTREE.parent / "asked-not-started"
PYTHON = str(WORKTREE.parents[1] / ".venv" / "bin" / "python")

EVALS = {
    "planning_card": (WORKTREE, "tests/integration/test_eval_planning_card_intent.py", "interpreter"),
    "day_frame": (WORKTREE, "tests/integration/test_eval_day_frame.py", "judge"),
    "timebox_question": (PEER_WORKTREE, "tests/integration/test_eval_timebox_question.py", "timeboxing"),
}

CONFIGS = {
    "pro-high": ("PRO", "high", None),
    "pro-high-1024": ("PRO", "high", 1024),
    "pro-high-2048": ("PRO", "high", 2048),
    "flash-minimal": ("FLASH", "minimal", None),
    "flash-minimal-1024": ("FLASH", "minimal", 1024),
    "flash-minimal-2048": ("FLASH", "minimal", 2048),
}


def _env_for(config: str, kind: str, base: dict) -> dict:
    pin, effort, cap = CONFIGS[config]
    model = base[f"OPENROUTER_DEFAULT_MODEL_{pin}"]
    env = dict(base)
    env["INTERPRETER_TIER_CONFIG"] = config
    if kind == "interpreter":
        env["LLM_MODEL_INTENT_INTERPRETER"] = model
        env["LLM_REASONING_EFFORT_INTENT_INTERPRETER"] = effort
        env["LLM_MAX_TOKENS_INTENT_INTERPRETER"] = str(cap if cap else 0)
    elif kind == "judge":
        env["LLM_MODEL_TIMEBOXING_JUDGE"] = model
        env["LLM_REASONING_EFFORT_TIMEBOXING_JUDGE"] = effort
        env["LLM_MAX_TOKENS"] = str(cap if cap else 0)
    else:  # the #328 eval, on its own worktree, takes the timeboxing agent's knobs
        env["LLM_MODEL_TIMEBOXING"] = model
        env["LLM_REASONING_EFFORT_TIMEBOXING"] = effort
        env["LLM_MAX_TOKENS"] = str(cap if cap else 0)
    return env


async def _run(config: str, name: str, out_dir: Path, base_env: dict) -> dict:
    worktree, rel, kind = EVALS[name]
    draws = out_dir / f"{config}--{name}.jsonl"
    junit = out_dir / f"{config}--{name}.xml"
    env = _env_for(config, kind, base_env)
    env["INTERPRETER_TIER_BENCH_OUT"] = str(draws)
    env["PYTHONPATH"] = f"{worktree / 'src'}:{HERE}"
    cmd = [PYTHON, "-m", "pytest", rel, "-m", "slow", "-q", "-s", "-p", "no:cacheprovider",
           "-p", "_interpreter_tier_plugin", f"--junitxml={junit}"]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=worktree, env=env,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    stdout, _ = await proc.communicate()
    (out_dir / f"{config}--{name}.log").write_bytes(stdout)
    return {"config": config, "eval": name, "returncode": proc.returncode, "draws": str(draws), "junit": str(junit)}


async def _pin_sequence(configs: list[str], out_dir: Path, base_env: dict) -> list[dict]:
    rows = []
    for config in configs:
        for name in EVALS:
            rows.append(await _run(config, name, out_dir, base_env))
    return rows


def _summarise(runs: list[dict], out_dir: Path, pricing: dict) -> dict:
    """Per configuration and eval: cases passed/total (from junit), lowest case,
    losses by error class, latency median/p90, completion-token median, cost."""
    import xml.etree.ElementTree as ET
    summary: dict = {}
    for run in runs:
        key = (run["config"], run["eval"])
        draws = [json.loads(line) for line in Path(run["draws"]).read_text().splitlines() if line.strip()]
        tree = ET.parse(run["junit"])
        cases = tree.getroot().iter("testcase")
        passed, total, failed_names = 0, 0, []
        for case in cases:
            total += 1
            if case.find("failure") is None and case.find("error") is None and case.find("skipped") is None:
                passed += 1
            elif case.find("skipped") is None:
                failed_names.append(case.get("name"))
        latencies = sorted(d["latency_s"] for d in draws if "latency_s" in d)
        completion = sorted(d["completion_tokens"] for d in draws if d.get("completion_tokens") is not None)
        errors = Counter(d["error"] for d in draws if "error" in d)
        length_finishes = sum(1 for d in draws if d.get("finish_reason") == "length")
        model = next((d.get("model") for d in draws if d.get("model")), None)
        in_p, out_p = pricing.get(model, (0.0, 0.0)) if model else (0.0, 0.0)
        cost = sum((d.get("prompt_tokens") or 0) * in_p + (d.get("completion_tokens") or 0) * out_p for d in draws) / 1e6
        summary[f"{key[0]}::{key[1]}"] = {
            "config": key[0], "eval": key[1], "model": model, "draws": len(draws),
            "cases_passed": passed, "cases_total": total, "failed_cases": failed_names,
            "transport_losses": dict(errors), "finish_length": length_finishes,
            "latency_median_s": statistics.median(latencies) if latencies else None,
            "latency_p90_s": latencies[int(0.9 * (len(latencies) - 1))] if latencies else None,
            "completion_tokens_median": statistics.median(completion) if completion else None,
            "cost_usd": round(cost, 4),
        }
    return summary


def _markdown(summary: dict, date: str) -> str:
    lines = [f"# Interpreter tier bench — {date}", "",
             "Tier one on tier one's pin (#336, #325). n=8 per case, no temperature pin.", ""]
    by_eval: dict = defaultdict(list)
    for row in summary.values():
        by_eval[row["eval"]].append(row)
    for name, rows in by_eval.items():
        lines += [f"## {name}", "", "| config | model | cases | failed | losses | finish=length | lat med | lat p90 | compl. tok med | cost |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda r: list(CONFIGS).index(r["config"])):
            lines.append(f"| {r['config']} | {r['model']} | {r['cases_passed']}/{r['cases_total']} | {', '.join(r['failed_cases']) or '—'} | {r['transport_losses'] or '—'} | {r['finish_length']} | {r['latency_median_s']:.2f}s | {r['latency_p90_s']:.2f}s | {r['completion_tokens_median']} | ${r['cost_usd']:.3f} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--configs", default=",".join(CONFIGS))
    args = parser.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set; source .env first", file=sys.stderr)
        return 2
    out_dir = HERE / f"interpreter-tier-{args.date}"
    out_dir.mkdir(exist_ok=True)
    base_env = dict(os.environ)
    wanted = [c for c in CONFIGS if c in args.configs.split(",")]
    pro = [c for c in wanted if c.startswith("pro")]
    flash = [c for c in wanted if c.startswith("flash")]

    async def race():
        return await asyncio.gather(_pin_sequence(pro, out_dir, base_env), _pin_sequence(flash, out_dir, base_env))

    pro_runs, flash_runs = asyncio.run(race())
    runs = pro_runs + flash_runs
    sys.path.insert(0, str(HERE))
    from report import pricing  # scripts/bench/report.py
    models = {json.loads(l).get("model") for r in runs for l in Path(r["draws"]).read_text().splitlines() if l.strip()} - {None}
    summary = _summarise(runs, out_dir, pricing(models))
    (HERE / f"results-interpreter-tier-{args.date}.json").write_text(json.dumps({"runs": runs, "summary": summary}, indent=2))
    (HERE / f"results-interpreter-tier-{args.date}.md").write_text(_markdown(summary, args.date))
    print((HERE / f"results-interpreter-tier-{args.date}.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Check `report.pricing`'s real signature and return shape and match it. Check the `-s` output of `test_eval_timebox_question.py` is captured to the `.log` (it is; `-s` prints the `[eval]` lines, which carry the decision breakdown per case — the summariser reads pass/fail from junit and the breakdown lives in the log; if you want the per-case decision counts in the JSON too, parse `[eval] <Kind> <n>/8 <- '<case>' :: {...}` lines from the two evals that print them, and say in the report which evals do not).

- [ ] **Step 3: Smoke the harness on one cheap configuration**

`cp ../../.env .env` if not done; `set -a; source .env; set +a`. Run:
`PYTHONPATH=src ../../.venv/bin/python scripts/bench/interpreter_tier.py --date smoke --configs flash-minimal-1024`
Expected: three logs, three JSONL draw files with `latency_s`, `completion_tokens`, `finish_reason`, `model` populated, a `results-interpreter-tier-smoke.md` with three tables. Fix the plugin's model attribute and the pricing call here if either is empty. Delete the `smoke` artefacts before the real run (`rm -r scripts/bench/interpreter-tier-smoke scripts/bench/results-interpreter-tier-smoke.*`).

- [ ] **Step 4: The real run**

`PYTHONPATH=src ../../.venv/bin/python scripts/bench/interpreter_tier.py --date 2026-09-06` (all six configurations; expect 60–90 minutes; if OpenRouter rate-limits, re-run the affected `--configs` and merge by re-running the summariser over the existing draw files — add a `--summarise-only` flag if needed, it is in scope). Keep the per-draw JSONL directory `scripts/bench/interpreter-tier-2026-09-06/` out of the commit if it exceeds a few MB (add it to `.gitignore` under `scripts/bench/interpreter-tier-*/`); the `results-*.json` (summary + run index) and the `.md` are committed.

- [ ] **Step 5: Read the numbers and set the cap**

In `results-interpreter-tier-2026-09-06.md`: for each capped configuration, `finish=length` > 0 means the cap bit. Decision rule from the spec §3: `_INTENT_INTERPRETER_MAX_TOKENS` = the smaller cap with zero bites on both pins; else 2048; else 0 with the case named on #325. Also name, in the markdown's summary section: any case that fell below 7/8 on **judgement** (not transport) on the flash pin that passed on the pro pin — that is the number Hugo's pin decision needs. Edit the constant in `factory.py` and the comment above it to cite the file.

**Superseded by what the bench actually returned.** The rule above lands on uncapped — both
1024 and 2048 truncated draws (3 of 545 and 4 of 546) — and Hugo overruled it on
`results-interpreter-tier-2026-09-06.md` and its `.reading.md` sidecar: every truncated draw
ran 6–56s against a 1–2s median, the largest legitimate uncapped answer was 405 completion
tokens, 2048 cut more draws than 1024 without buying one back, and no case failed on length —
so the truncated draws were runaways stopped, not answers lost. **Ruling:
`_INTENT_INTERPRETER_MAX_TOKENS = 1024`.** The pin, separately, landed on the pro pin at
`high` (effort `low` since 2026-09-11, `results-interpreter-tier-2026-09-11.md`) rather than the
flash pin at `minimal`: the prompts as written lose on flash — 4
judgement losses against the pro pin's 0, revision-after-commit 1/8 against 8/8 (the raw case
counts, 27/34 against 32/34, fold in the break-it families and are not the pin comparison) —
and the flash pin waits on #406.
`docs/reference/setup/llm.md`'s "Surface interpreter model" section carries both rulings for
an incoming reader; this step's rule is what was planned, not what landed.

- [ ] **Step 6: Unit suite, then commit**

`PYTHONPATH=src ../../.venv/bin/python -m pytest tests/unit/test_intent_interpreter_client.py tests -m "not slow" -q` → only the pre-existing failure. `git status` must not show `.env` or the smoke artefacts.

```bash
git add scripts/bench/_interpreter_tier_plugin.py scripts/bench/interpreter_tier.py scripts/bench/results-interpreter-tier-2026-09-06.json scripts/bench/results-interpreter-tier-2026-09-06.md src/fateforger/llm/factory.py
# plus .gitignore if the draws directory was ignored
git commit -m "bench(llm): the surface interpreters on tier one's pin against today's, and the cap the numbers pick (#336, #325)

Six configurations, three evals, n=8 per case: today's client (pro, high) and
the flash pin at minimal, each uncapped and capped at 1024 and 2048. Per draw:
latency, tokens, finish reason, error class -- transport kept apart from
judgement. The cap default is set to the smaller cap that bit nowhere; the
pin line stays Hugo's, with the judgement-loss column beside it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** §1 site, rows, sites switched, guard → Task 1. §2 bench matrix, plugin, runner, outputs, transport-vs-judgement rule → Task 2. §3 landing the cap → Task 2 Step 5. §4 tests → Task 1 Step 1 (unit, sites, guard); the eval-on-the-landed-default proof is the PR's job (controller). "What this does not do" → nothing planned for it; `.env` untouched throughout.

**Placeholder scan.** Two "check the real signature" instructions (`report.pricing`, the client's model attribute) are verification against code the plan could not read at authoring time, each with the fallback stated.

**Type consistency.** `INTENT_INTERPRETER`, `build_intent_interpreter_client`, `_INTENT_INTERPRETER_MAX_TOKENS` named identically in Tasks 1 and 2; env names `LLM_MODEL_INTENT_INTERPRETER` / `LLM_REASONING_EFFORT_INTENT_INTERPRETER` / `LLM_MAX_TOKENS_INTENT_INTERPRETER` identical in Task 1's settings and Task 2's `_env_for`; the `-1 / 0 / >0` convention stated once in Task 1 and honoured by `_env_for` writing `0` for uncapped.
