from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("autogen_core")

from autogen_core.tools import FunctionTool

from fateforger.agents.tasks.list_tools import TickTickListManager
from fateforger.agents.tasks.notion_sprint_tools import NotionSprintManager
from fateforger.haunt.tools import build_haunting_tools


def test_tasks_strict_tools_expose_valid_schema() -> None:
    ticktick = TickTickListManager(server_url="http://example.invalid/mcp", workbench=object())
    notion = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=object())

    tools = [
        FunctionTool(
            ticktick.manage_ticktick_lists,
            name="manage_ticktick_lists",
            description="x",
            strict=True,
        ),
        FunctionTool(
            notion.find_sprint_items,
            name="find_sprint_items",
            description="x",
            strict=True,
        ),
        FunctionTool(
            notion.link_sprint_subtasks,
            name="link_sprint_subtasks",
            description="x",
            strict=True,
        ),
        FunctionTool(
            notion.patch_sprint_page_content,
            name="patch_sprint_page_content",
            description="x",
            strict=True,
        ),
    ]

    for tool in tools:
        schema = tool.schema
        assert schema.get("strict") is True


def test_haunt_strict_tools_expose_valid_schema() -> None:
    tools = build_haunting_tools(service=object())  # type: ignore[arg-type]
    for tool in tools:
        schema = tool.schema
        assert schema.get("strict") is True


def test_strict_functiontool_targets_do_not_use_default_args() -> None:
    violations: list[str] = []

    for path in Path("src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        class_methods: dict[str, dict[str, ast.arguments]] = {}
        module_functions: dict[str, ast.arguments] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_functions[node.name] = node.args
            if isinstance(node, ast.ClassDef):
                methods: dict[str, ast.arguments] = {}
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods[child.name] = child.args
                class_methods[node.name] = methods

        def _args_have_defaults(args: ast.arguments) -> bool:
            if args.defaults:
                return True
            return any(default is not None for default in args.kw_defaults)

        def _resolve_attribute_target(
            call_node: ast.Call, attr_node: ast.Attribute
        ) -> ast.arguments | None:
            if isinstance(attr_node.value, ast.Name):
                var_name = attr_node.value.id
                method_name = attr_node.attr

                # Resolve simple local class instances, e.g. toolbox = HauntingToolbox(...)
                for stmt in ast.walk(tree):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                        continue
                    if stmt.targets[0].id != var_name:
                        continue
                    if not isinstance(stmt.value, ast.Call):
                        continue
                    if not isinstance(stmt.value.func, ast.Name):
                        continue
                    class_name = stmt.value.func.id
                    class_map = class_methods.get(class_name, {})
                    if method_name in class_map:
                        return class_map[method_name]

            if (
                isinstance(attr_node.value, ast.Attribute)
                and isinstance(attr_node.value.value, ast.Name)
                and attr_node.value.value.id == "self"
            ):
                # Resolve self._x = ClassName(...) patterns for strict tool registration.
                self_attr = attr_node.value.attr
                method_name = attr_node.attr
                for stmt in ast.walk(tree):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    if len(stmt.targets) != 1:
                        continue
                    target = stmt.targets[0]
                    if not (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        continue
                    if target.attr != self_attr:
                        continue
                    if not isinstance(stmt.value, ast.Call):
                        continue
                    if not isinstance(stmt.value.func, ast.Name):
                        continue
                    class_name = stmt.value.func.id
                    class_map = class_methods.get(class_name, {})
                    if method_name in class_map:
                        return class_map[method_name]

            _ = call_node
            return None

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Name) and node.func.id == "FunctionTool"
            ):
                continue

            strict_true = any(
                kw.arg == "strict"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if not strict_true or not node.args:
                continue

            target = node.args[0]
            args_obj: ast.arguments | None = None
            target_label = ast.unparse(target) if hasattr(ast, "unparse") else "<callable>"

            if isinstance(target, ast.Name):
                args_obj = module_functions.get(target.id)
            elif isinstance(target, ast.Attribute):
                args_obj = _resolve_attribute_target(node, target)

            if args_obj is None:
                continue
            if _args_have_defaults(args_obj):
                violations.append(f"{path}:{target_label}")

    assert not violations, "strict FunctionTool callables use default args: " + ", ".join(
        violations
    )
