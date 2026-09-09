"""Per-line coverage diff between two `coverage json` reports.

Usage:
    python covdiff.py <before.json> <after.json> <source-root>

Run from the directory holding the two report files -- paths are taken
literally (relative or absolute), so a basename resolves against the
caller's cwd. The reports themselves store source paths relative to
wherever pytest ran (typically the repo root), which is rarely the same
place as the two report files when this is run as a one-off diagnostic
from a scratch directory -- so the required third argument names the
root those paths resolve against for the on-disk existence check. There
is no cwd-based default: a report's paths resolving against the wrong
root doesn't fail, it silently resolves every path to "does not exist"
and misfiles every regression under "deleted" instead of "surviving" --
the exact way this script was once run wrong. To guard against a root
that is merely a *different* wrong directory (also silent), refuse to
run when fewer than half the before-report's paths exist under it.

For every file the *before* report measured, this compares the set of
covered (`executed_lines`) line numbers against the same file in the
*after* report. A file that dropped out of the after-report entirely
counts as having lost every line it had covered. The verdict that
matters is whether the file still exists on disk: a deleted file losing
its covered lines is expected (nothing new can exercise code that is
gone); a *surviving* file losing covered lines is a regression -- some
line that used to run is no longer reached by any test.

A surviving file's line diff can also be a false positive if that file
was itself edited between the two coverage captures: an edit shifts
every line after it, so a line merely moved reads as a line lost. This
script cannot tell that apart from a genuine regression -- check `git
diff` on any surviving file this reports before trusting the number.
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
    if len(sys.argv) != 4:
        print(
            f"usage: {sys.argv[0]} <before.json> <after.json> <source-root>",
            file=sys.stderr,
        )
        raise SystemExit(2)

    before = json.loads(pathlib.Path(sys.argv[1]).read_text())
    after = json.loads(pathlib.Path(sys.argv[2]).read_text())
    root = pathlib.Path(sys.argv[3])

    before_paths = sorted(before.get("files", {}))
    if before_paths:
        resolved = sum(1 for p in before_paths if (root / p).exists())
        if resolved < len(before_paths) / 2:
            print(
                f"refusing: only {resolved}/{len(before_paths)} of the before-report's "
                f"paths exist under source-root {root} -- wrong root, every regression "
                "would be misfiled as 'deleted'",
                file=sys.stderr,
            )
            raise SystemExit(1)

    deleted: list[tuple[str, list[int]]] = []
    surviving: list[tuple[str, list[int]]] = []

    for path in before_paths:
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
