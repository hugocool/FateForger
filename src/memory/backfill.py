# src/memory/backfill.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from memory.models import Channel
from memory.service import MemoryService, ObserveOutcome


class LegacyRow(BaseModel):
    name: str
    description: str
    thread_ts: str | None
    created_at: datetime


class BackfillReport(BaseModel):
    rows_read: int = 0
    stored: int = 0
    suppressed: dict[str, int] = Field(default_factory=dict)
    constraints_created: int = 0
    folds: int = 0
    durable: int = 0
    outcomes: list[ObserveOutcome] = Field(default_factory=list)


def read_profile_rows(legacy_db_path: str) -> list[LegacyRow]:
    """PROFILE rows from the legacy store, oldest first.

    Read-only; nothing here inspects meaning. Ordering matters because each
    row's canonicalise depends on the constraints prior rows created.
    """
    conn = sqlite3.connect(legacy_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, description, thread_ts, created_at "
        "FROM timeboxing_constraints WHERE scope = 'PROFILE' "
        "ORDER BY created_at"
    ).fetchall()
    out: list[LegacyRow] = []
    for r in rows:
        try:
            when = datetime.fromisoformat(str(r["created_at"]))
        except ValueError:
            when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        out.append(
            LegacyRow(
                name=r["name"] or "",
                description=r["description"] or "",
                thread_ts=r["thread_ts"],
                created_at=when,
            )
        )
    return out


def _text(row: LegacyRow) -> str:
    # Serialisation for the judge to read — not a judgement about meaning.
    if row.name and row.description:
        return f"{row.name}: {row.description}"
    return row.name or row.description


async def backfill(legacy_db_path: str, service: MemoryService) -> BackfillReport:
    """Replay legacy PROFILE rows through the real pipeline, sequentially.

    Deliberately does NO dedup of its own: dedup (within-thread) and
    canonicalise (cross-thread) are the only permitted ways to decide two
    rows mean the same thing. Legacy identity never crosses — every
    observation is minted fresh (I3). A judge failure stops the run: a
    half-poisoned store found later is worse than a stopped backfill.
    """
    report = BackfillReport()
    seen_constraints: set[str] = set()
    for row in read_profile_rows(legacy_db_path):
        report.rows_read += 1
        outcome = await service.observe(
            _text(row),
            channel=Channel.PLANNING,
            session_id=row.thread_ts or "legacy:no-thread",
            observed_at=row.created_at,
        )
        report.outcomes.append(outcome)
        if not outcome.stored:
            key = outcome.suppressed_as or "unknown"
            report.suppressed[key] = report.suppressed.get(key, 0) + 1
            continue
        report.stored += 1
        if outcome.constraint_uid in seen_constraints:
            report.folds += 1
        else:
            seen_constraints.add(outcome.constraint_uid)
            report.constraints_created += 1
    report.durable = sum(
        1 for o in report.outcomes if o.tier is not None and o.tier.value == "durable"
    )
    return report


def main() -> None:
    import asyncio
    import os
    import sys

    from memory.openrouter_judge import openrouter_judge_from_env

    legacy = sys.argv[1] if len(sys.argv) > 1 else "data/admonish.db"
    target = sys.argv[2] if len(sys.argv) > 2 else "data/memory.db"
    if os.path.exists(target):
        raise SystemExit(
            f"{target} already exists; refusing to seed over it. "
            f"Move it aside first."
        )
    service = MemoryService(target, openrouter_judge_from_env())
    report = asyncio.run(backfill(legacy, service))
    print(report.model_dump_json(indent=2, exclude={"outcomes"}))
    for o in report.outcomes:
        mark = "stored" if o.stored else f"suppressed:{o.suppressed_as}"
        print(f"  [{mark:18s}] {o.constraint_name or ''}")


if __name__ == "__main__":
    main()
