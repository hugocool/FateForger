# tests/unit/test_referents_never_match_gist.py
"""The gist is content, and content is only ever read by a judge.

Block titles are the one field in the descriptor that came from a plan rather
than from a column, so they are the one field someone might be tempted to
compare, sort or filter on. CLAUDE.md forbids it, and a wrong pattern does not
raise -- it quietly returns the wrong answer forever. So it is asserted.
"""

from __future__ import annotations

import ast
from pathlib import Path

import fateforger.referents as referents_pkg

PACKAGE = Path(referents_pkg.__file__).parent

#: Names that would mean code is reading the gist's *meaning* rather than
#: passing it along. `in`/`sorted`/`.lower()` over a title is the shape.
FORBIDDEN_METHODS = {"lower", "upper", "casefold", "strip", "split", "startswith", "endswith", "find", "index", "replace"}

#: Built-in functions that make decisions about content and must not be called on gist.
#: Excluded: list, tuple, iter (these are plumbing that pass values through unchanged).
FORBIDDEN_BUILTINS = {"sorted", "any", "all", "max", "min", "sum", "set"}


def _gist_attribute_names(tree: ast.AST) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "gist"
    ]


def _is_direct_gist_reference(node: ast.AST) -> bool:
    """Check if node is directly self.gist or a slice of self.gist (e.g., self.gist[:N]).

    Returns False if .gist appears only deeper in the expression tree.
    """
    if isinstance(node, ast.Attribute) and node.attr == "gist":
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "gist":
        return True
    return False


def test_no_module_in_the_package_imports_re():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(a.name != "re" for a in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert node.module != "re", path


def test_no_string_method_is_called_on_anything_reached_through_gist():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in FORBIDDEN_METHODS:
                continue
            # Walk the receiver looking for `.gist`
            assert not _gist_attribute_names(func.value), f"{path}: {func.attr} on gist"


def test_no_builtin_with_decision_logic_is_called_on_gist():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            func = call.func
            if not isinstance(func, ast.Name):
                continue
            if func.id not in FORBIDDEN_BUILTINS:
                continue
            # Check if any argument directly reaches `.gist` or a slice of it
            # (not when .gist appears only deeper in nested expressions)
            for arg in call.args:
                assert not _is_direct_gist_reference(arg), f"{path}: {func.id}() on gist"


def test_the_gist_is_never_a_comparison_operand():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                for operand in operands:
                    assert not _gist_attribute_names(operand), f"{path}: gist compared"


def test_the_package_imports_no_slack():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # Check plain imports like `import slack_sdk` or `import slack.client`
                for alias in node.names:
                    assert "slack" not in alias.name, f"{path}: imports {alias.name}"
            if isinstance(node, ast.ImportFrom):
                # Check from imports like `from slack_sdk import ...`
                if node.module:
                    assert "slack" not in node.module, f"{path}: imports from {node.module}"
