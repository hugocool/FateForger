"""Per-line coverage diff between two `coverage json` reports.

Usage:
    python covdiff.py <before.json> <after.json> [source-root]

Run from the directory holding the two report files -- paths are taken
literally (relative or absolute), so a basename resolves against the
caller's cwd. The reports themselves store source paths relative to
wherever pytest ran (typically the repo root), which is rarely the same
place as the two report files when this is run as a one-off diagnostic
from a scratch directory -- so the optional third argument names the
root those paths resolve against for the on-disk existence check.
Defaults to the caller's cwd.

For every file the *before* report measured, this compares the set of
covered (`executed_lines`) line numbers against the same file in the
*after* report. A file that dropped out of the after-report entirely
counts as having lost every line it had covered. The verdict that
matters is whether the file still exists on disk: a deleted file losing
its covered lines is expected (nothing new can exercise code that is
gone); a *surviving* file losing covered lines is a regression -- some
line that used to run is no longer reached by any test.
"""

from __future__ import annotations

import json
import pathlib
import sys


def _executed(report: dict, path: str) -> set[int]:
    entry = report.get("files", {}).get(path)
    if not entry:
        return set()
    return set(entry.get("executed_lines", []))


def main() -> None:
    if len(sys.argv) not in (3, 4):
        print(
            f"usage: {sys.argv[0]} <before.json> <after.json> [source-root]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    before = json.loads(pathlib.Path(sys.argv[1]).read_text())
    after = json.loads(pathlib.Path(sys.argv[2]).read_text())
    root = pathlib.Path(sys.argv[3]) if len(sys.argv) == 4 else pathlib.Path.cwd()

    deleted: list[tuple[str, list[int]]] = []
    surviving: list[tuple[str, list[int]]] = []

    for path in sorted(before.get("files", {})):
        lost = sorted(_executed(before, path) - _executed(after, path))
        if not lost:
            continue
        target = (deleted if not (root / path).exists() else surviving)
        target.append((path, lost))

    print(f"before: {len(before.get('files', {}))} files measured")
    print(f"after:  {len(after.get('files', {}))} files measured")
    print()
    print(f"=== deleted files that lost covered lines ({len(deleted)}) ===")
    for path, lost in deleted:
        print(f"  {path}  (-{len(lost)} lines)")
    print()
    print(f"=== SURVIVING files that lost covered lines ({len(surviving)}) ===")
    if not surviving:
        print("  none")
    for path, lost in surviving:
        shown = ", ".join(str(n) for n in lost[:20])
        more = f" (+{len(lost) - 20} more)" if len(lost) > 20 else ""
        print(f"  {path}:{shown}{more}")


if __name__ == "__main__":
    main()
