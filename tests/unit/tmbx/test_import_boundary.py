"""tmbx must never import from fateforger. The reverse is allowed."""
from __future__ import annotations

import ast
from pathlib import Path

TMBX_ROOT = Path(__file__).resolve().parents[3] / "src" / "tmbx"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_tmbx_never_imports_fateforger():
    offenders: list[str] = []
    for py in TMBX_ROOT.rglob("*.py"):
        for mod in _imported_modules(py):
            if mod == "fateforger" or mod.startswith("fateforger."):
                offenders.append(f"{py.relative_to(TMBX_ROOT)} imports {mod}")
    assert offenders == [], "tmbx must not import fateforger:\n" + "\n".join(offenders)


def test_tmbx_package_exists():
    import tmbx

    assert tmbx.__name__ == "tmbx"
