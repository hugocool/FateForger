"""Where the repository root is, found rather than counted.

Tests used to spell this ``Path(__file__).resolve().parents[2]``, which encodes
how deep the test file happens to sit. Moving a test one directory down then
breaks it in a way that looks like a missing file. Walk up to the marker
instead, and the answer is right from any depth.
"""

from __future__ import annotations

from pathlib import Path

ROOT: Path = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file()
)

__all__ = ["ROOT"]
