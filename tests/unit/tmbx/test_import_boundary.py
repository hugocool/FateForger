"""tmbx must never import from fateforger. The reverse is allowed."""
from __future__ import annotations

import ast
from pathlib import Path

TMBX_ROOT = Path(__file__).resolve().parents[3] / "src" / "tmbx"


def _imported_modules(path: Path) -> set[str]:
    """Extract module names from both static and dynamic imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    for node in ast.walk(tree):
        # Static imports: import x, import x.y
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        # Static imports: from x import y
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        # Dynamic imports: __import__("fateforger"), importlib.import_module("fateforger")
        elif isinstance(node, ast.Call):
            target_name = None
            if isinstance(node.func, ast.Name):
                target_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                # importlib.import_module
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib":
                    target_name = f"importlib.{node.func.attr}"

            if target_name in ("__import__", "importlib.import_module") and node.args:
                if isinstance(node.args[0], ast.Constant):
                    found.add(node.args[0].value)

    return found


def test_tmbx_never_imports_fateforger() -> None:
    """Scan tmbx source for any direct or dynamic imports of fateforger."""
    assert TMBX_ROOT.is_dir(), f"TMBX_ROOT path {TMBX_ROOT} does not exist"

    py_files = list(TMBX_ROOT.rglob("*.py"))
    assert py_files, f"No Python files found in {TMBX_ROOT} — path may be invalid"

    offenders: list[str] = []
    for py in py_files:
        for mod in _imported_modules(py):
            if mod == "fateforger" or mod.startswith("fateforger."):
                offenders.append(f"{py.relative_to(TMBX_ROOT)} imports {mod}")
    assert offenders == [], "tmbx must not import fateforger:\n" + "\n".join(offenders)


def test_tmbx_package_exists() -> None:
    """Verify tmbx package is importable."""
    import tmbx

    assert tmbx.__name__ == "tmbx"


def test_dynamic_import_dunder_caught() -> None:
    """Verify __import__("fateforger") is detected."""
    source = '__import__("fateforger")'
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "__import__" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    found.add(node.args[0].value)
    assert "fateforger" in found, f"Dynamic __import__ not detected; found: {found}"


def test_dynamic_import_importlib_caught() -> None:
    """Verify importlib.import_module("fateforger.core") is detected."""
    source = 'importlib.import_module("fateforger.core")'
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target_name = None
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib":
                    target_name = f"importlib.{node.func.attr}"

            if target_name == "importlib.import_module" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    found.add(node.args[0].value)
    assert "fateforger.core" in found, f"Dynamic importlib.import_module not detected; found: {found}"
