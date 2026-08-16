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
                # <any>.import_module (catches both importlib and aliased imports)
                if node.func.attr == "import_module":
                    target_name = "import_module"

            if target_name in ("__import__", "import_module") and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
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


def test_dynamic_import_dunder_caught(tmp_path: Path) -> None:
    """Verify __import__("fateforger") via _imported_modules()."""
    test_file = tmp_path / "test_dunder.py"
    test_file.write_text('__import__("fateforger")\n')
    found = _imported_modules(test_file)
    assert "fateforger" in found, f"__import__(\"fateforger\") not detected; found: {found}"


def test_dynamic_import_importlib_caught(tmp_path: Path) -> None:
    """Verify importlib.import_module("fateforger.core") via _imported_modules()."""
    test_file = tmp_path / "test_importlib.py"
    test_file.write_text('importlib.import_module("fateforger.core")\n')
    found = _imported_modules(test_file)
    assert "fateforger.core" in found, f"importlib.import_module(\"fateforger.core\") not detected; found: {found}"


def test_dynamic_import_aliased_importlib_caught(tmp_path: Path) -> None:
    """Verify aliased importlib.import_module("fateforger") via _imported_modules()."""
    test_file = tmp_path / "test_alias.py"
    test_file.write_text('import importlib as il\nil.import_module("fateforger")\n')
    found = _imported_modules(test_file)
    assert "fateforger" in found, f"Aliased il.import_module(\"fateforger\") not detected; found: {found}"


def test_innocent_import_not_flagged(tmp_path: Path) -> None:
    """Verify innocent imports are not mistakenly flagged."""
    test_file = tmp_path / "test_innocent.py"
    test_file.write_text('import os\nfrom pathlib import Path\n')
    found = _imported_modules(test_file)
    assert "fateforger" not in found
    assert not any(m.startswith("fateforger.") for m in found)
