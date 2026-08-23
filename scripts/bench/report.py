#!/usr/bin/env python3
"""Turn a bench run into the two tables that decide the model choice."""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

TASK_ORDER = ["route", "parallel_reads", "stage_prose", "progress_line", "patch"]


def med(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def fmt(value, suffix="", width=6):
    return f"{value:.2f}{suffix}".rjust(width) if value is not None else "  —".rjust(width)


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    by = defaultdict(list)
    for cell in data["cells"]:
        by[(cell["contender"], cell["task"])] = cell["samples"]

    contenders = sorted({c for c, _ in by}, key=lambda c: "LIVE" not in c)

    print(f"prefix: {data['system_chars']} chars system + {data['tools_full']} tools")
    print(f"samples per cell: {data['samples']}\n")

    print("=" * 104)
    print("TTFT — median seconds to the first token a person can see (reasoning deltas excluded)")
    print("=" * 104)
    head = "contender".ljust(34) + "".join(t[:13].rjust(15) for t in TASK_ORDER)
    print(head)
    print("-" * 104)
    for c in contenders:
        row = c.ljust(34)
        for t in TASK_ORDER:
            samples = by.get((c, t), [])
            ok = [s for s in samples if not s["error"]]
            row += fmt(med(s["ttft"] for s in ok), "s", 15)
        print(row)

    print()
    print("=" * 104)
    print("THROUGHPUT — median visible output tokens/sec, and reasoning tokens burnt per call")
    print("=" * 104)
    print("contender".ljust(34) + "tok/s (prose)".rjust(16) + "reasoning tok".rjust(16)
          + "route total".rjust(16) + "tool calls".rjust(14))
    print("-" * 104)
    for c in contenders:
        prose = [s for s in by.get((c, "stage_prose"), []) if not s["error"]]
        tps = med(
            (s["out_tokens"] / s["total"]) if s["total"] and s["out_tokens"] else None
            for s in prose
        )
        allok = [s for s in sum((by.get((c, t), []) for t in TASK_ORDER), []) if not s["error"]]
        reasoning = med(s.get("reasoning_tokens") for s in allok)
        route = [s for s in by.get((c, "route"), []) if not s["error"]]
        rt = med(s["total"] for s in route)
        par = [s for s in by.get((c, "parallel_reads"), []) if not s["error"]]
        calls = med(s["tool_calls"] for s in par)
        print(c.ljust(34) + fmt(tps, "", 16) + fmt(reasoning, "", 16)
              + fmt(rt, "s", 16) + fmt(calls, "", 14))

    print()
    print("=" * 104)
    print("FAILURES")
    print("=" * 104)
    quiet = True
    for (c, t), samples in sorted(by.items()):
        errs = [s["error"] for s in samples if s["error"]]
        if errs:
            quiet = False
            print(f"{c} / {t}: {len(errs)}/{len(samples)} — {errs[0][:120]}")
    if quiet:
        print("none")

    print()
    print("=" * 104)
    print("SAMPLE OUTPUT — the progress line, which is what the speed is for")
    print("=" * 104)
    for c in contenders:
        samples = [s for s in by.get((c, "progress_line"), []) if not s["error"] and s["text"]]
        if samples:
            print(f"\n{c}:")
            for s in samples[:2]:
                print(f"    {s['text'].strip()[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
