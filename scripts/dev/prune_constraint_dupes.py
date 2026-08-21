#!/usr/bin/env python3
"""One-time migration: prune duplicate PROFILE/DATESPAN constraints from the local SQLite store.

The ConstraintStore.add_constraints() has dedup logic, but existing rows inserted before
or via different code paths have accumulated duplicates. This script groups by semantic key
(scope + name + rule_kind + dates) and archives all but the highest-ranked row per group.

Usage:
    poetry run python scripts/dev/prune_constraint_dupes.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from fateforger.agents.timeboxing.preferences import (  # noqa: E402
    Constraint,
    ConstraintScope,
    ConstraintStatus,
    _constraint_canonical_rank,
    _constraint_semantic_key,
)

DB_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/admonish.db")
if DB_URL.startswith("sqlite:///"):
    DB_URL = DB_URL.replace("sqlite:///", "sqlite+aiosqlite:///", 1)


async def main(dry_run: bool) -> None:
    engine = create_async_engine(DB_URL)
    sm: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as session:
        stmt = select(Constraint).where(
            Constraint.scope.in_([ConstraintScope.PROFILE, ConstraintScope.DATESPAN]),
            Constraint.status != ConstraintStatus.DECLINED,
        )
        result = await session.execute(stmt)
        rows: list[Constraint] = list(result.scalars().all())

    print(f"Found {len(rows)} active PROFILE/DATESPAN constraints.")

    grouped: dict[str, list[Constraint]] = {}
    for row in rows:
        key = _constraint_semantic_key(row)
        grouped.setdefault(key, []).append(row)

    to_archive: list[int] = []
    for key, group in grouped.items():
        if len(group) <= 1:
            continue
        ranked = sorted(group, key=_constraint_canonical_rank)
        canonical = ranked[0]
        dupes = ranked[1:]
        print(
            f"  Duplicate group ({len(group)}): {canonical.name!r} [scope={canonical.scope.value}]"
            f" — keeping id={canonical.id} (status={canonical.status.value})"
            f", archiving ids={[d.id for d in dupes]}"
        )
        to_archive.extend(d.id for d in dupes if d.id is not None)

    if not to_archive:
        print("No duplicates found.")
        await engine.dispose()
        return

    print(f"\n{'Would archive' if dry_run else 'Archiving'} {len(to_archive)} duplicate rows.")

    if not dry_run:
        async with sm() as session:
            await session.execute(
                update(Constraint)
                .where(Constraint.id.in_(to_archive))
                .values(status=ConstraintStatus.DECLINED)
            )
            await session.commit()
        print("Done.")
    else:
        print("Dry run — no changes written. Re-run without --dry-run to apply.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
