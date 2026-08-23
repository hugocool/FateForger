"""Seed curated preference statements into the KG memory store.

Additive: nothing is deleted. Each statement goes in as an *observation* and the
memory server's own extraction derives the constraint, so provenance survives
and `reproject()` can re-derive later. Injecting constraints directly would be
faster and would leave rows with no observation behind them.

Runs in-process with OpenRouterJudge rather than through the MCP on 8010, so the
model is pinned here (CLAUDE.md names this class for offline corpus passes)
instead of inherited from whichever host happens to answer.

`write_uid` is deterministic per statement: L1 is append-only, so a retry
without it would look exactly like the user saying the same thing twice and
would inflate the evidence that promotion counts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.models import Channel, Provenance  # noqa: E402
from memory.openrouter_judge import OpenRouterJudge  # noqa: E402
from memory.service import MemoryService  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--observations", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--tag", required=True, help="write_uid prefix, keeps retries safe")
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    items = json.loads(Path(args.observations).read_text())
    key, base = os.environ.get("OPENROUTER_API_KEY"), os.environ.get("OPENROUTER_BASE_URL")
    if not key or not base:
        print("OPENROUTER_API_KEY / OPENROUTER_BASE_URL not set", file=sys.stderr)
        return 2

    judge = OpenRouterJudge(api_key=key, base_url=base)
    service = MemoryService(args.db, judge)
    sem = asyncio.Semaphore(args.concurrency)
    now = datetime.now(timezone.utc)
    ok, failed = [], []

    async def one(i: int, item: dict):
        text = item["statement"]
        async with sem:
            try:
                outcome = await service.observe(
                    text,
                    channel=Channel.PLANNING,
                    session_id=args.tag,
                    observed_at=now,
                    provenance=Provenance.OBSERVED,
                    write_uid=f"{args.tag}-{i:03d}",
                )
                ok.append((text, outcome))
                print(f"  [{i + 1}/{len(items)}] ok: {text[:68]}")
            except Exception as exc:
                failed.append((text, exc))
                print(f"  [{i + 1}/{len(items)}] FAILED {type(exc).__name__}: {exc}")

    try:
        print(f"seeding {len(items)} observations into {args.db} (concurrency {args.concurrency})")
        await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
    finally:
        await judge.aclose()

    print(f"\nseeded ok: {len(ok)}   failed: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
