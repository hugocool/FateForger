from __future__ import annotations

import ast
import asyncio
import inspect

from fateforger.core import runtime as runtime_module


def _runtime_attributes_set_by(function_name: str) -> set[str]:
    """The names `setattr(runtime, "<name>", …)` binds inside one function.

    Read from the source: the function builds Slack clients, a scheduler and an
    MCP calendar workbench before it reaches the wiring, so a test that called
    it would be testing the environment. The attribute names are identifiers
    this system minted.
    """

    tree = ast.parse(inspect.getsource(runtime_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == function_name:
            return {
                call.args[1].value
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "setattr"
                and len(call.args) == 3
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "runtime"
                and isinstance(call.args[1], ast.Constant)
            }
    raise AssertionError(f"{function_name} not found in fateforger.core.runtime")


def test_the_runtime_publishes_the_required_block_rule_for_the_dispatcher() -> None:
    """R3: `dispatch_planning_reminder` revalidates every required-block rung
    against `runtime.required_block_rule`, and drops the reminder when the
    attribute is absent. Wiring it is what makes those reminders reachable."""

    assert "required_block_rule" in _runtime_attributes_set_by("_create_runtime")


class _FakeGuardian:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    async def reconcile_all(self) -> None:
        self.calls += 1
        if self.mode == "ok":
            return
        if self.mode == "timeout":
            await asyncio.sleep(0.05)
            return
        raise RuntimeError("boom")


async def test_run_initial_planning_reconcile_success() -> None:
    guardian = _FakeGuardian(mode="ok")
    result = await runtime_module._run_initial_planning_reconcile(  # noqa: SLF001
        planning_guardian=guardian,
        timeout_s=0.01,
    )
    assert result is True
    assert guardian.calls == 1


async def test_run_initial_planning_reconcile_timeout() -> None:
    guardian = _FakeGuardian(mode="timeout")
    result = await runtime_module._run_initial_planning_reconcile(  # noqa: SLF001
        planning_guardian=guardian,
        timeout_s=0.001,
    )
    assert result is False
    assert guardian.calls == 1


async def test_run_initial_planning_reconcile_error() -> None:
    guardian = _FakeGuardian(mode="error")
    result = await runtime_module._run_initial_planning_reconcile(  # noqa: SLF001
        planning_guardian=guardian,
        timeout_s=0.01,
    )
    assert result is False
    assert guardian.calls == 1
