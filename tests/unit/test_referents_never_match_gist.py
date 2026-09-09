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


def _gist_attribute_names(tree: ast.AST) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "gist"
    ]


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
            module = (
                node.module
                if isinstance(node, ast.ImportFrom)
                else None
            )
            if module:
                assert "slack" not in module.split("."), f"{path}: imports {module}"
